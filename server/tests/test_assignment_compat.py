"""合并 phase2（A07）后：编写指导 / 参考资料 / AI 编写沿用的 require_scope_write、require_manage 按 A07 生效负责人判断。"""

from __future__ import annotations

import pytest

from app import document_assignments as da
from app.errors import AppError

NODES = [da.Node("h1", "heading", 1, "# 1"), da.Node("p1", "paragraph", None, "甲"),
         da.Node("h2", "heading", 1, "# 2"), da.Node("p2", "paragraph", None, "乙")]


@pytest.fixture
def scope(monkeypatch):
    state = {"role": "editor", "admin": False, "mode": "enforced"}
    monkeypatch.setattr(da, "actor_scope", lambda *_a: (state["role"], state["admin"], state["mode"]))
    monkeypatch.setattr(da, "nodes_of", lambda *_a: NODES)
    monkeypatch.setattr(da, "explicit_map", lambda *_a: {"h1": frozenset({"A"}), "h2": frozenset({"B"})})
    return state


def test_restricted_editor_only_own_objects(scope):
    da.require_scope_write(None, "d", "A", ["p1", "h1"])
    with pytest.raises(AppError) as error:
        da.require_scope_write(None, "d", "A", ["p1", "p2"])
    assert error.value.status_code == 403 and error.value.details["objectIds"] == ["p2"]
    with pytest.raises(AppError):
        da.require_scope_write(None, "d", "A")      # 整篇操作
    with pytest.raises(AppError):
        da.require_manage(None, "d", "A")


@pytest.mark.parametrize("change", [{"mode": "open"}, {"role": "owner"}, {"admin": True}])
def test_unrestricted_cases_pass(scope, change):
    scope.update(change)
    da.require_scope_write(None, "d", "A", ["p2"])
    da.require_scope_write(None, "d", "A")
    da.require_manage(None, "d", "A")
    assert not da.assignment_restricted(None, "d", "A")
