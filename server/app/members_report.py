"""项目成员清单（01 §3.3：迁移 110 前后各输出一份供核对；审查 S10）。

    python -m app.members_report [--out FILE]

负责人（owner）已被移出、项目里没有任何 owner 的项目单独列在 projectsWithoutOwner：
迁移不自动提升任何人，由管理员事后指定负责人。
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("app.migrate")


def members_snapshot(connection) -> dict:
    """只读：每个项目的 owner_id、成员与角色。connection 可为 DictConnection 或 sqlite3.Connection。"""
    def rows(sql: str) -> list[dict]:
        cursor = connection.execute(sql)
        names = [column[0] for column in cursor.description]
        return [{name: row[name] for name in names} if hasattr(row, "keys") else dict(zip(names, row))
                for row in cursor.fetchall()]

    projects = rows("SELECT p.id, p.name, p.status, p.owner_id, u.name AS owner_name FROM projects p "
                    "LEFT JOIN users u ON u.id = p.owner_id ORDER BY p.created_at, p.id")
    members = rows("SELECT m.project_id, m.user_id, m.role, u.name FROM project_members m "
                   "LEFT JOIN users u ON u.id = m.user_id ORDER BY m.project_id, m.role, m.user_id")
    by_project: dict[str, list[dict]] = {}
    for member in members:
        by_project.setdefault(str(member["project_id"]), []).append(
            {"userId": str(member["user_id"]), "name": member["name"], "role": member["role"]})
    result = []
    for project in projects:
        listed = by_project.get(str(project["id"]), [])
        result.append({"projectId": str(project["id"]), "name": project["name"], "status": project["status"],
                       "ownerId": str(project["owner_id"]), "ownerName": project["owner_name"], "members": listed})
    return {"projects": result, "projectsWithoutOwner": without_owner(result)}


def without_owner(projects: list[dict]) -> list[dict]:
    found = []
    for project in projects:
        if any(member["role"] == "owner" for member in project["members"]):
            continue
        listed = next((m for m in project["members"] if m["userId"] == project["ownerId"]), None)
        reason = "OWNER_REMOVED" if listed is None else "OWNER_DEMOTED"
        found.append({"projectId": project["projectId"], "name": project["name"], "ownerId": project["ownerId"],
                      "ownerName": project["ownerName"], "reason": reason,
                      "note": "项目没有负责人，需管理员指定（迁移不自动提升）。"})
    return found


def diff(before: dict, after: dict) -> list[dict]:
    old = {p["projectId"]: {m["userId"]: m["role"] for m in p["members"]} for p in before["projects"]}
    changes = []
    for project in after["projects"]:
        previous = old.get(project["projectId"], {})
        for member in project["members"]:
            if previous.get(member["userId"]) != member["role"]:
                changes.append({"projectId": project["projectId"], "name": project["name"], "userId": member["userId"],
                                "userName": member["name"], "from": previous.get(member["userId"]), "to": member["role"]})
    return changes


def write_migration_report(directory: Path, version: str, before: dict, after: dict) -> Path | None:
    """迁移前后清单写到数据库所在目录的 migration-reports/；写失败只记日志，不影响迁移。"""
    report = {"migration": version, "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "changes": diff(before, after), "projectsWithoutOwner": after["projectsWithoutOwner"],
              "before": before, "after": after}
    for project in after["projectsWithoutOwner"]:
        logger.warning("项目「%s」(%s) 没有负责人（%s），请管理员指定。", project["name"], project["projectId"], project["reason"])
    try:
        target = directory / "migration-reports"
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{version}-{report['generatedAt'].replace(':', '')}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.warning("迁移 %s 成员清单已写入 %s（变更 %d 条，无负责人项目 %d 个）。", version, path,
                       len(report["changes"]), len(report["projectsWithoutOwner"]))
        return path
    except OSError as error:
        logger.error("迁移 %s 成员清单写入失败：%s", version, error)
        return None


def _cli(argv: list[str] | None = None) -> int:
    from app.db import connect

    parser = argparse.ArgumentParser(prog="python -m app.members_report", description="输出项目成员清单（只读）")
    parser.add_argument("--out", type=Path, help="写到文件；不给则输出到标准输出")
    args = parser.parse_args(argv)
    with connect() as connection:
        snapshot = members_snapshot(connection)
    text = json.dumps(snapshot, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
