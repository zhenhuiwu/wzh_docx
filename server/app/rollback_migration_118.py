"""停服务后回滚对象分工；整库备份保留分工和审计，有分工时须显式 --force。"""

import argparse
from datetime import datetime
from pathlib import Path
import sqlite3
import sys

from app.rollback_migration_114 import _database_path, _in_use, refuse_if_later

VERSION = "118_document_assignments"
TABLES = ("document_assignment_events", "document_object_assignments")
COLUMNS = ("assignments_enabled", "assignments_revision")


def rollback(path: Path, apply: bool, force: bool = False) -> int:
    if not path.exists():
        print(f"没有找到数据库：{path}", file=sys.stderr)
        return 1
    connection = sqlite3.connect(path, timeout=30)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
        recorded = "schema_migrations" in tables and connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?", (VERSION,)).fetchone() is not None
        if not recorded and not tables.intersection(TABLES) and not columns.intersection(COLUMNS):
            print("迁移 118 未执行过，不需要回滚。")
            return 0
        if refuse_if_later(connection, 118):
            return 2
        busy = _in_use(path, connection)
        if busy:
            print(f"服务似乎正在运行（{busy}），请先停止服务。", file=sys.stderr)
            if apply:
                return 2
        used = sum(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                   for table in TABLES if table in tables)
        if "assignments_enabled" in columns:
            used += connection.execute("SELECT count(*) FROM documents WHERE assignments_enabled=1").fetchone()[0]
        if used:
            print("回滚会移除分工保护和分工审计；旧程序将按项目编辑权限工作。恢复须使用整库备份。", file=sys.stderr)
            if apply and not force:
                print("请确认后加 --force。", file=sys.stderr)
                return 3
        if not apply:
            print("只检查，数据库未改动。")
            return 0
        backup = path.with_name(f"{path.name}.before-rollback-118-{datetime.now():%Y%m%d-%H%M%S-%f}.db")
        target = sqlite3.connect(backup)
        try:
            connection.backup(target)
        finally:
            target.close()
        connection.execute("BEGIN IMMEDIATE")
        try:
            for table in TABLES:
                if table in tables:
                    connection.execute(f"DROP TABLE {table}")
            for column in COLUMNS:
                if column in columns:
                    connection.execute(f"ALTER TABLE documents DROP COLUMN {column}")
            if recorded:
                connection.execute("DELETE FROM schema_migrations WHERE version=?", (VERSION,))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        print(f"已回滚迁移 118，备份：{backup}。请使用 118 之前的程序启动。")
        return 0
    finally:
        connection.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="回滚文档分工迁移 118")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--force", action="store_true", help="确认移除已有分工保护和审计")
    parser.add_argument("--service-stopped", action="store_true")
    args = parser.parse_args(argv)
    path = _database_path()
    if args.yes and path.exists():
        connection = sqlite3.connect(path, timeout=30)
        try:
            busy = _in_use(path, connection)
        finally:
            connection.close()
        if busy:
            print(f"服务似乎正在运行（{busy}），请先停止服务。", file=sys.stderr)
            return 2
    if args.yes and not args.service_stopped:
        if not sys.stdin.isatty() or input("确认服务已停止，请输入 yes：").strip().lower() != "yes":
            print("未执行；非交互使用时需先停服务并加 --service-stopped。", file=sys.stderr)
            return 2
    return rollback(path, args.yes, args.force)


if __name__ == "__main__":
    sys.exit(main())
