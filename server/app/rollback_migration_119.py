"""单独回滚迁移 119（document_baselines：文档版本与项目基线）。

先停服务，用与服务相同的 DATABASE_URL 运行::

    uv run python -m app.rollback_migration_119          # 只检查，不改库
    uv run python -m app.rollback_migration_119 --yes --service-stopped
    uv run python -m app.rollback_migration_119 --yes --force   # 已有版本或基线时必须加

在同一事务里删掉 project_baseline_items、project_baselines、document_baseline_sections、
document_baselines、document_baseline_blobs，并删除 schema_migrations 里的 119 记录。
执行前整库备份为 <库名>.before-rollback-119-<时间>.db。文档正文不受影响。
有更新的迁移（120 及以后）还在时拒绝（退出码 2），按从新到旧的顺序回滚。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from app.rollback_migration_114 import _database_path, _in_use, refuse_if_later

VERSION = "119_document_baselines"
NUMBER = 119
TABLES = ("project_baseline_items", "project_baselines", "document_baseline_sections",
          "document_baselines", "document_baseline_blobs")


def _state(connection: sqlite3.Connection) -> tuple[bool, list[str]]:
    recorded = connection.execute(
        "select 1 from sqlite_master where type = 'table' and name = 'schema_migrations'").fetchone() is not None \
        and connection.execute("select 1 from schema_migrations where version = ?", (VERSION,)).fetchone() is not None
    tables = [name for name in TABLES
              if connection.execute("select 1 from sqlite_master where type='table' and name=?", (name,)).fetchone()]
    return recorded, tables


def rollback(path: Path, apply: bool, force: bool = False) -> int:
    if not path.exists():
        print(f"没有找到数据库：{path}", file=sys.stderr)
        return 1
    connection = sqlite3.connect(path, timeout=30)
    try:
        recorded, tables = _state(connection)
        if not (recorded or tables):
            print("迁移 119 未执行过，不需要回滚。")
            return 0
        if refuse_if_later(connection, NUMBER):
            return 2
        busy = _in_use(path, connection)
        if busy:
            print(f"服务似乎正在运行（{busy}）。请先停止使用该库的服务，再执行回滚。", file=sys.stderr)
            if apply:
                return 2
        rows = sum(connection.execute(f"select count(*) from {t}").fetchone()[0]
                   for t in tables if t in ("document_baselines", "project_baselines"))
        if rows and not force:
            print("已有文档版本或项目基线：回滚会删除它们。确认后加 --force。", file=sys.stderr)
            if apply:
                return 3
        if not apply:
            print("只做了检查。确认已停服务后加 --yes 执行回滚。")
            return 0
        if rows:
            backup = path.with_name(f"{path.name}.before-rollback-119-{datetime.now():%Y%m%d-%H%M%S}.db")
            target = sqlite3.connect(backup)
            try:
                connection.backup(target)
            finally:
                target.close()
            print(f"已备份到：{backup}")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for table in tables:
                connection.execute(f"DROP TABLE {table}")
            if recorded:
                connection.execute("delete from schema_migrations where version = ?", (VERSION,))
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        print("已回滚迁移 119。")
        return 0
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="单独回滚迁移 119（文档版本与项目基线）")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--service-stopped", action="store_true")
    args = parser.parse_args(argv)
    path = _database_path()
    if args.yes and not args.service_stopped:
        if not sys.stdin.isatty():
            print("非交互运行时请在人工确认服务已停止后加 --service-stopped。", file=sys.stderr)
            return 2
        if input("确认所有使用该库的服务都已停止，请输入 yes：").strip().lower() != "yes":
            return 2
    return rollback(path, args.yes, args.force)


if __name__ == "__main__":
    sys.exit(main())
