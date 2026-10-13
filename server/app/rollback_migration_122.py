"""单独回滚迁移 122（upstream_baseline_ack：document_upstreams.acked_baseline_seq）。

**先停服务**，用与服务相同的 DATABASE_URL 运行::

    uv run python -m app.rollback_migration_122          # 只检查，不改库
    uv run python -m app.rollback_migration_122 --yes --service-stopped --force   # 已有数据时必须加 --force

删除 document_upstreams.acked_baseline_seq 列；有已核对记录时需 --force，先整库备份。有更新的迁移（123 及以后）还在时拒绝（退出码 2）。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from app.rollback_migration_114 import _database_path, _in_use, refuse_if_later

NUMBER = 122
VERSION = "122_upstream_baseline_ack"
TABLE, COLUMN = "document_upstreams", "acked_baseline_seq"


def rollback(path: Path, apply: bool, force: bool = False) -> int:
    if not path.exists():
        print(f"没有找到数据库：{path}", file=sys.stderr)
        return 1
    connection = sqlite3.connect(path, timeout=30)
    try:
        recorded = connection.execute("select 1 from sqlite_master where type='table' and name='schema_migrations'").fetchone() \
            is not None and connection.execute("select 1 from schema_migrations where version=?", (VERSION,)).fetchone() is not None
        has_column = COLUMN in {r[1] for r in connection.execute(f"pragma table_info({TABLE})")}
        if not (recorded or has_column):
            print(f"迁移 {NUMBER} 未执行过，不需要回滚。")
            return 0
        if refuse_if_later(connection, NUMBER):
            return 2
        busy = _in_use(path, connection)
        if busy and apply:
            print(f"服务似乎正在运行（{busy}）。请先停止使用该库的服务，再执行回滚。", file=sys.stderr)
            return 2
        acked = connection.execute(f"select count(*) from {TABLE} where {COLUMN} is not null").fetchone()[0] if has_column else 0
        if acked and not force:
            print(f"已有 {acked} 条“已核对”记录：回滚会丢掉它们。确认后加 --force。", file=sys.stderr)
            if apply:
                return 3
        if not apply:
            print(f"只做了检查。确认已停服务后加 --yes 执行回滚。")
            return 0
        backup = path.with_name(f"{path.name}.before-rollback-{NUMBER}-{datetime.now():%Y%m%d-%H%M%S}.db")
        target_db = sqlite3.connect(backup)
        try:
            connection.backup(target_db)
        finally:
            target_db.close()
        connection.execute("BEGIN IMMEDIATE")
        try:
            if has_column:
                connection.execute(f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}")
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
