"""Offline contract checks. Run with: cd server; python -m unittest discover -s tests -p test_document_plans.py"""

from __future__ import annotations

import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError

from app import document_plans as plans
from app.errors import AppError


class Result:
    def __init__(self, one=None, many=None):
        self.one = one
        self.many = many or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class OfflineConnection:
    def __init__(self):
        self.role = "editor"
        self.template_exists = True
        self.duplicate = False
        self.count = 0
        self.queries = []
        self.plan = None
        self.archived_at = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def transaction(self):
        return self

    def execute(self, sql, params=()):
        self.queries.append((sql, params))
        if "from project_members" in sql:
            return Result({"role": self.role} if self.role else None)
        if "select archived_at from projects" in sql:  # A05 R3：归档项目只读
            return Result({"archived_at": self.archived_at})
        if "from templates where id" in sql:
            return Result({"id": params[0]} if self.template_exists else None)
        if "from document_plans where project_id" in sql and "count(*)" not in sql:
            return Result({"id": uuid4()} if self.duplicate else None)
        if "count(*)" in sql:
            return Result({"total": self.count})
        if "insert into document_plans" in sql:
            self.plan = {
                "id": params[0], "name": params[2], "purpose": params[3],
                "template_id": params[4], "template_name": None, "template_status": None,
                "document_id": None,
            }
            return Result()
        if "from document_plans p" in sql:
            return Result(self.plan, [self.plan] if self.plan else [])
        return Result({"id": params[0]} if params else None)


class DocumentPlanTests(unittest.TestCase):
    def setUp(self):
        self.project_id = str(uuid4())
        self.actor = SimpleNamespace(user_id=uuid4(), roles=("project_creator",), csrf_token="token")
        self.request = SimpleNamespace(method="POST", headers={"x-csrf-token": "token"}, state=SimpleNamespace(request_id="offline-id"))
        self.connection = OfflineConnection()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(plans, "connect", return_value=self.connection))
        self.stack.enter_context(patch.object(plans, "_load", return_value={}))
        self.stack.enter_context(patch("app.identity.require_session", return_value=self.actor))

    def body(self, **overrides):
        return plans.DocumentPlanBody.model_validate({"name": "  验收报告  ", "purpose": " 归档 ", **overrides})

    def create(self, body=None):
        return plans.create_document_plan(self.project_id, self.request, body or self.body())

    def test_archived_project_rejects_new_plans(self):
        self.connection.archived_at = "2026-10-11 00:00:00"
        with self.assertRaises(AppError) as raised:
            self.create()
        self.assertEqual((raised.exception.status_code, raised.exception.code), (409, "PROJECT_ARCHIVED"))
        self.assertIsNone(self.connection.plan)

    def test_offline_create_and_response_never_claim_template_is_published(self):
        result = self.create()
        self.assertEqual(result["requestId"], "offline-id")
        self.assertEqual(result["data"]["name"], "验收报告")
        self.assertEqual(result["data"]["purpose"], "归档")
        self.assertIsNone(result["data"]["templateId"])
        self.assertIsNone(result["data"]["templateStatus"])
        self.assertIsNone(result["data"]["documentId"])
        self.assertTrue(any("insert into document_plans" in sql for sql, _ in self.connection.queries))
        self.assertTrue(any("for update" in sql for sql, _ in self.connection.queries))
        self.assertFalse(any("insert into documents " in sql for sql, _ in self.connection.queries))

    def test_trimmed_empty_and_invalid_fields(self):
        for field in ("name", "purpose"):
            with self.subTest(field=field), self.assertRaises(AppError) as caught:
                self.create(self.body(**{field: "  "}))
            self.assertEqual(caught.exception.status_code, 422)
            self.assertEqual(caught.exception.field_errors[0]["field"], field)
        for data in ({"name": "a" * 121}, {"purpose": "a" * 201}, {"templateId": "invalid"}, {"unexpected": 1}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                self.body(**data)

    def test_project_role_decides_without_global_capability(self):
        # 决策1（阶段2 S1）：只有项目编写角色、没有全局 documents.edit 也可以建计划。
        self.actor.roles = ("auditor",)
        self.assertEqual(self.create()["data"]["name"], "验收报告")
        self.connection.plan = None
        self.connection.queries.clear()
        self.actor.roles = ("project_creator",)
        self.connection.role = "viewer"
        with self.assertRaises(AppError) as caught:
            self.create()
        self.assertEqual(caught.exception.status_code, 403)
        self.assertFalse(any("insert into document_plans" in sql for sql, _ in self.connection.queries))
        self.actor.roles = ("system_admin",)
        self.assertEqual(self.create()["data"]["name"], "验收报告")

    def test_post_requires_csrf(self):
        self.request.headers = {}
        with self.assertRaises(AppError) as caught:
            self.create()
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(self.connection.queries, [])

    def test_missing_project_remains_not_found(self):
        with patch.object(plans, "_load", side_effect=AppError(404, "NOT_FOUND", "项目不存在或你无权访问。")):
            with self.assertRaises(AppError) as caught:
                self.create()
        self.assertEqual(caught.exception.status_code, 404)
        self.assertFalse(any("insert into document_plans" in sql for sql, _ in self.connection.queries))

    def test_unknown_template_duplicate_and_limit(self):
        self.connection.template_exists = False
        with self.assertRaises(AppError) as caught:
            self.create(self.body(templateId=str(uuid4())))
        self.assertEqual(caught.exception.status_code, 422)
        self.connection.duplicate = True
        with self.assertRaises(AppError) as caught:
            self.create()
        self.assertEqual(caught.exception.code, "DUPLICATE_NAME")
        self.connection.duplicate = False
        self.connection.count = 100
        with self.assertRaises(AppError) as caught:
            self.create()
        self.assertEqual(caught.exception.code, "LIMIT_REACHED")

    def test_list_pagination_join_and_visibility(self):
        self.request.method = "GET"
        self.request.headers = {}  # GET does not require CSRF.
        self.connection.plan = {
            "id": uuid4(), "name": "验收报告", "purpose": "归档", "template_id": uuid4(),
            "template_name": "候选模板", "template_status": "draft", "document_id": uuid4(),
        }
        self.connection.count = 1
        response = plans.list_document_plans(self.project_id, self.request, page=2, pageSize=10)
        self.assertEqual(response["data"]["total"], 1)
        self.assertEqual(response["data"]["items"][0]["templateStatus"], "draft")
        self.assertEqual(response["data"]["items"][0]["documentId"], str(self.connection.plan["document_id"]))
        sql, params = self.connection.queries[-1]
        self.assertIn("left join documents d", sql)
        self.assertIn("d.name = p.name", sql)
        self.assertNotIn("d.purpose = p.purpose", sql)
        self.assertEqual(params[-2:], (10, 10))
        with self.assertRaises(AppError) as caught:
            plans.list_document_plans(self.project_id, self.request, pageSize=51)
        self.assertEqual(caught.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
