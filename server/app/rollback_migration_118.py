"""单独回滚迁移 118（document_assignments：A07 段落 / 章节负责人，硬限制）。

只在需要退回到 118 之前的程序版本时使用；**先停服务**，用与服务相同的 DATABASE_URL 运行::

    uv run python -m app.rollback_migration_118          # 只检查，不改库
    uv run python -m app.rollback_migration_118 --yes --service-stopped   # 非交互运行时
    uv run python -m app.rollback_migration_118 --yes --force            # 已有分配或被拒保存时必须加

回滚在同一事务里（17 §4.9“回滚”）：
- 删掉 document_object_assignments、document_rejected_saves、document_annotation_events（连同索引）；
- ALTER TABLE documents DROP COLUMN assignment_mode、assignments_revision；
- 删掉 schema_migrations 里的 118 记录。
注意：
- 已有分配或被拒保存时默认拒绝（分工和被拒内容会丢失），确认后加 --force；三张表先整表导出 CSV
  （<库名>.<表>-118-<时间>.csv），再整库备份为 <库名>.before-rollback-118-<时间>.db；
- 回滚后编辑权退回“项目角色”（编写人员可改整篇）；
- **回滚后必须用 118 之前的旧版本程序启动服务**，否则启动时会重新执行 118；
- 有更新的迁移（119 及以后）还在时拒绝（退出码 2），按从新到旧的顺序回滚：118 → 117 → 116 → 115 → 114。
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from app.rollback_migration_114 import _database_path, _in_use, refuse_if_later

VERSION = "118_document_assignments"
TABLES = ("document_object_assignments", "document_rejected_saves", "document_annotation_events")
COLUMNS = (("documents", "assignment_mode"), ("documents", "assignments_revision"))


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"pragma table_info({table})")}


def _state(connection: sqlite3.Connection) -> dict[str, object]:
    recorded = connection.execute(
        "select 1 from sqlite_master where type = 'table' and name = 'schema_migrations'"
    ).fetchone() is not None and connection.execute(
        "select 1 from schema_migrations where version = ?", (VERSION,)
    ).fetchone() is not None
    tables = [name for name in TABLES
              if connection.execute("select 1 from sqlite_master where type = 'table' and name = ?", (name,)).fetchone()]
    columns = [(table, column) for table, column in COLUMNS if column in _columns(connection, table)]
    return {"recorded": recorded, "tables": tables, "columns": columns}


def _export(connection: sqlite3.Connection, path: Path, table: str) -> tuple[Path, int]:
    target = path.with_name(f"{path.name}.{table}-118-{datetime.now():%Y%m%d-%H%M%S}.csv")
    cursor = connection.execute(f"select * from {table}")
    names = [column[0] for column in cursor.description]
    count = 0
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(names)
        for row in cursor:
            writer.writerow(row)
            count += 1
    return target, count


def rollback(path: Path, apply: bool, force: bool = False) -> int:
    if not path.exists():
        print(f"没有找到数据库：{path}", file=sys.stderr)
        return 1
    connection = sqlite3.connect(path, timeout=30)
    try:
        state = _state(connection)
        if not (state["recorded"] or state["tables"] or state["columns"]):
            print("迁移 118 未执行过，不需要回滚。")
            return 0
        if refuse_if_later(connection, 118):
            return 2
        counts = {table: connection.execute(f"select count(*) from {table}").fetchone()[0] for table in state["tables"]}
        print(f"数据库：{path}")
        print(f"迁移记录：{'有' if state['recorded'] else '无'}；" + "；".join(f"{t} {n} 行" for t, n in counts.items()))
        busy = _in_use(path, connection)
        if busy:
            print(f"服务似乎正在运行（{busy}）。请先停止使用该库的服务，再执行回滚。", file=sys.stderr)
            if apply:
                return 2
        valuable = counts.get("document_object_assignments", 0) + counts.get("document_rejected_saves", 0)
        if valuable and not force:
            print("已有分工或被拒保存：回滚会删除它们（编辑权退回项目角色）。确认后加 --force。", file=sys.stderr)
            if apply:
                return 3
        if not apply:
            print("只做了检查。确认已停服务后加 --yes 执行回滚；回滚后必须用 118 之前的旧版本程序启动，否则会重新执行 118。")
            return 0
        for table in state["tables"]:
            exported, count = _export(connection, path, table)
            print(f"已导出 {table} {count} 行到：{exported}")
        backup = path.with_name(f"{path.name}.before-rollback-118-{datetime.now():%Y%m%d-%H%M%S}.db")
        target = sqlite3.connect(backup)
        try:
            connection.backup(target)
        finally:
            target.close()
        print(f"已备份到：{backup}")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for table in state["tables"]:
                connection.execute(f"DROP TABLE {table}")
            for table, column in state["columns"]:
                connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
            if state["recorded"]:
                connection.execute("delete from schema_migrations where version = ?", (VERSION,))
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        print("已回滚迁移 118。现在请用 118 之前的旧版本程序启动服务（新版本启动会重新执行 118）。")
        return 0
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="单独回滚迁移 118（段落 / 章节负责人）")
    parser.add_argument("--yes", action="store_true", help="确认执行（默认只检查）")
    parser.add_argument("--force", action="store_true", help="已有分工或被拒保存时仍然回滚")
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
        if not sys.stdin.isatty():
            print("非交互运行时请在人工确认服务已停止后加 --service-stopped。", file=sys.stderr)
            return 2
        if input("确认所有使用该库的服务都已停止，请输入 yes：").strip().lower() != "yes":
            print("已取消，库未改动。")
            return 2
    return rollback(path, args.yes, args.force)


if __name__ == "__main__":
    sys.exit(main())
