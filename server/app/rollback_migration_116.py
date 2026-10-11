"""单独回滚迁移 116（project_members_v2：A05 成员独立版本号、成员行时间与操作人、成员变更审计）。

只在需要退回到 116 之前的程序版本时使用；**先停服务**，用与服务相同的 DATABASE_URL 运行::

    uv run python -m app.rollback_migration_116          # 只检查，不改库
    uv run python -m app.rollback_migration_116 --yes    # 先整库备份、导出审计 CSV，再回滚（会要求输入 yes 确认已停服务）
    uv run python -m app.rollback_migration_116 --yes --service-stopped   # 非交互运行时

回滚在同一事务里做这些事：
- 删掉 project_member_events 表（连同它的索引）和 idx_project_members_user 索引；
- ALTER TABLE … DROP COLUMN：projects.members_revision，project_members.created_at / updated_at / updated_by
  （SQLite ≥ 3.35 支持；这些列没有索引、外键和约束，满足 DROP COLUMN 的条件）；
- 删掉 schema_migrations 里的 116 记录。
注意：
- **成员变更审计会丢失**。回滚前脚本先把 project_member_events 整表导出为 CSV（与库同目录，
  <库名>.project_member_events-116-<时间>.csv），再整库备份；成员关系本身（project_members 的 project_id/user_id/role/revision）不受影响。
- 回滚后成员“加入时间、最近调整人”丢失；116 之前的程序用项目 revision 校验成员修改，不受影响。
- 回滚顺序：116 → 115 → 114（要退到 115 之前时先回滚 116，再依次运行 rollback_migration_115、rollback_migration_114）。
- 备份文件与库同目录，名为 <库名>.before-rollback-116-<时间>.db；需要撤销回滚时停服务后用它覆盖原库即可。
- **回滚后必须用 116 之前的旧版本程序启动服务**，否则启动时会自动重新执行 116（列和表重新建出来，审计为空、成员时间为 NULL）。
- 服务在运行时脚本拒绝回滚（退出码 2）；能否检测到运行中的服务与 rollback_migration_114 相同（见该文件说明）。
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from app.rollback_migration_114 import _database_path, _in_use

VERSION = "116_project_members_v2"
TABLE = "project_member_events"
MEMBER_INDEX = "idx_project_members_user"
PROJECT_COLUMNS = ("members_revision",)
MEMBER_COLUMNS = ("created_at", "updated_at", "updated_by")


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"pragma table_info({table})")}


def _state(connection: sqlite3.Connection) -> dict[str, object]:
    recorded = connection.execute(
        "select 1 from sqlite_master where type = 'table' and name = 'schema_migrations'"
    ).fetchone() is not None and connection.execute(
        "select 1 from schema_migrations where version = ?", (VERSION,)
    ).fetchone() is not None
    has_table = connection.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?", (TABLE,)
    ).fetchone() is not None
    has_index = connection.execute(
        "select 1 from sqlite_master where type = 'index' and name = ?", (MEMBER_INDEX,)
    ).fetchone() is not None
    project_columns = [name for name in PROJECT_COLUMNS if name in _columns(connection, "projects")]
    member_columns = [name for name in MEMBER_COLUMNS if name in _columns(connection, "project_members")]
    return {"recorded": recorded, "table": has_table, "index": has_index,
            "project_columns": project_columns, "member_columns": member_columns}


def _export_events(connection: sqlite3.Connection, path: Path) -> tuple[Path, int]:
    target = path.with_name(f"{path.name}.{TABLE}-116-{datetime.now():%Y%m%d-%H%M%S}.csv")
    cursor = connection.execute(f"select * from {TABLE} order by created_at, id")
    names = [column[0] for column in cursor.description]
    count = 0
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(names)
        for row in cursor:
            writer.writerow(row)
            count += 1
    return target, count


def rollback(path: Path, apply: bool) -> int:
    if not path.exists():
        print(f"没有找到数据库：{path}", file=sys.stderr)
        return 1
    connection = sqlite3.connect(path, timeout=30)
    try:
        state = _state(connection)
        if not (state["recorded"] or state["table"] or state["index"] or state["project_columns"] or state["member_columns"]):
            print("迁移 116 未执行过，不需要回滚。")
            return 0
        events = connection.execute(f"select count(*) from {TABLE}").fetchone()[0] if state["table"] else 0
        dated = connection.execute(
            "select count(*) from project_members where created_at is not null or updated_at is not null"
        ).fetchone()[0] if state["member_columns"] else 0
        print(f"数据库：{path}")
        print(f"迁移记录：{'有' if state['recorded'] else '无'}；{TABLE} 表：{'有' if state['table'] else '无'}（{events} 条成员变更审计，回滚前导出 CSV）；"
              f"成员行带时间的 {dated} 行（回滚后时间与操作人丢失，成员关系不变）。")
        busy = _in_use(path, connection)
        if busy:
            print(f"服务似乎正在运行（{busy}）。请先停止使用该库的服务，再执行回滚。", file=sys.stderr)
            if apply:
                return 2
        if not apply:
            print("只做了检查。确认已停服务后加 --yes 执行回滚；回滚后必须用 116 之前的旧版本程序启动，否则会重新执行 116。")
            return 0
        if state["table"]:
            exported, count = _export_events(connection, path)
            print(f"已导出 {count} 条成员变更审计到：{exported}")
        backup = path.with_name(f"{path.name}.before-rollback-116-{datetime.now():%Y%m%d-%H%M%S}.db")
        target = sqlite3.connect(backup)
        try:
            connection.backup(target)
        finally:
            target.close()
        print(f"已备份到：{backup}")
        connection.execute("BEGIN IMMEDIATE")
        try:
            if state["table"]:
                connection.execute(f"DROP TABLE {TABLE}")
            if state["index"]:
                connection.execute(f"DROP INDEX {MEMBER_INDEX}")
            for name in state["project_columns"]:
                connection.execute(f"ALTER TABLE projects DROP COLUMN {name}")
            for name in state["member_columns"]:
                connection.execute(f"ALTER TABLE project_members DROP COLUMN {name}")
            if state["recorded"]:
                connection.execute("delete from schema_migrations where version = ?", (VERSION,))
            connection.execute("COMMIT")
        except sqlite3.Error:
            connection.execute("ROLLBACK")
            raise
        print("已回滚迁移 116。现在请用 116 之前的旧版本程序启动服务（新版本启动会重新执行 116）。")
        return 0
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="单独回滚迁移 116（成员独立版本号、成员时间与成员变更审计）")
    parser.add_argument("--yes", action="store_true", help="确认执行（默认只检查）")
    parser.add_argument("--service-stopped", action="store_true", help="非交互运行时声明：已人工确认所有使用该库的服务都已停止")
    args = parser.parse_args(argv)
    path = _database_path()
    if args.yes and path.exists():
        connection = sqlite3.connect(path, timeout=30)
        try:
            busy = _in_use(path, connection)
        finally:
            connection.close()
        if busy:
            print(f"服务似乎正在运行（{busy}）。请先停止使用该库的服务，再执行回滚。", file=sys.stderr)
            return 2
    if args.yes and not args.service_stopped:
        prompt = "确认所有使用该库的服务都已停止，请输入 yes："
        if not sys.stdin.isatty():
            print("非交互运行时请在人工确认服务已停止后加 --service-stopped。", file=sys.stderr)
            return 2
        if input(prompt).strip().lower() != "yes":
            print("已取消，库未改动。")
            return 2
    return rollback(path, args.yes)


if __name__ == "__main__":
    sys.exit(main())
