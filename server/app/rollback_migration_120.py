"""单独回滚迁移 120（tasks_templates：模板上游、文档上游、任务与任务章节、公共框架块）。

**先停服务**，用与服务相同的 DATABASE_URL 运行::

    uv run python -m app.rollback_migration_120          # 只检查，不改库
    uv run python -m app.rollback_migration_120 --yes --service-stopped --force   # 已有数据时必须加 --force

只 DROP 本迁移新建的 6 张表，不改既有表；任务写回的 A07 分工保留在 document_object_assignments 里。
有数据时先整表导出 CSV、整库备份。有更新的迁移（121 及以后）还在时拒绝（退出码 2）。
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from app.rollback_migration_114 import _database_path, _in_use, refuse_if_later

NUMBER = 120
VERSION = "120_tasks_templates"
TABLES = ("public_block_refs", "public_blocks", "project_task_targets", "project_tasks",
          "document_upstreams", "template_document_upstreams")


def rollback(path: Path, apply: bool, force: bool = False) -> int:
    if not path.exists():
        print(f"没有找到数据库：{path}", file=sys.stderr)
        return 1
    connection = sqlite3.connect(path, timeout=30)
    try:
        recorded = connection.execute("select 1 from sqlite_master where type='table' and name='schema_migrations'").fetchone() \
            is not None and connection.execute("select 1 from schema_migrations where version=?", (VERSION,)).fetchone() is not None
        tables = [t for t in TABLES if connection.execute("select 1 from sqlite_master where type='table' and name=?", (t,)).fetchone()]
        if not (recorded or tables):
            print(f"迁移 {NUMBER} 未执行过，不需要回滚。")
            return 0
        if refuse_if_later(connection, NUMBER):
            return 2
        counts = {t: connection.execute(f"select count(*) from {t}").fetchone()[0] for t in tables}
        print(f"数据库：{path}；" + "；".join(f"{t} {n} 行" for t, n in counts.items()))
        busy = _in_use(path, connection)
        if busy and apply:
            print(f"服务似乎正在运行（{busy}）。请先停止使用该库的服务，再执行回滚。", file=sys.stderr)
            return 2
        if any(counts.values()) and not force:
            print("已有任务、上游链接或公共内容：回滚会删除它们。确认后加 --force。", file=sys.stderr)
            if apply:
                return 3
        if not apply:
            print(f"只做了检查。确认已停服务后加 --yes 执行回滚；回滚后必须用 {NUMBER} 之前的程序启动。")
            return 0
        stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
        for table in tables:
            if not counts[table]:
                continue
            target = path.with_name(f"{path.name}.{table}-{NUMBER}-{stamp}.csv")
            cursor = connection.execute(f"select * from {table}")
            with target.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow([c[0] for c in cursor.description])
                writer.writerows(cursor)
            print(f"已导出 {table} 到：{target}")
        backup = path.with_name(f"{path.name}.before-rollback-{NUMBER}-{stamp}.db")
        target_db = sqlite3.connect(backup)
        try:
            connection.backup(target_db)
        finally:
            target_db.close()
        print(f"已备份到：{backup}")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for table in tables:
                connection.execute(f"DROP TABLE {table}")
            if recorded:
                connection.execute("delete from schema_migrations where version=?", (VERSION,))
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        print(f"已回滚迁移 {NUMBER}。")
        return 0
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"单独回滚迁移 {NUMBER}（任务与模板）")
    parser.add_argument("--yes", action="store_true", help="确认执行（默认只检查）")
    parser.add_argument("--force", action="store_true", help="已有数据时仍然回滚")
    parser.add_argument("--service-stopped", action="store_true", help="已人工确认所有使用该库的服务都已停止")
    args = parser.parse_args(argv)
    if args.yes and not args.service_stopped and not sys.stdin.isatty():
        print("非交互运行时请加 --service-stopped 声明已停服务。", file=sys.stderr)
        return 2
    return rollback(_database_path(), apply=args.yes, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
