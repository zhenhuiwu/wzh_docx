"""单独回滚迁移 114（document_objects.deleted_after_ref）。

只在需要退回到 114 之前的程序版本时使用；先停服务，用与服务相同的 DATABASE_URL 运行::

    uv run python -m app.rollback_migration_114          # 只检查，不改库
    uv run python -m app.rollback_migration_114 --yes    # 先整库备份，再回滚

回滚做两件事（同一事务）：删掉 document_objects.deleted_after_ref 列、删掉 schema_migrations 里的 114 记录。
注意：
- 该列记录“删除时紧挨在前面的对象”，回滚后这些位置信息丢失；已删除段落“恢复为新段落”回退到章节路径 + 序号定位（与 114 之前一致）。
- 备份文件与库同目录，名为 <库名>.before-rollback-114-<时间>.db；需要撤销回滚时停服务后用它覆盖原库即可。
- 回滚后若仍启动含 114 的新版本，服务启动时会自动重新执行 114（列重新加上，旧删除记录的该列为 NULL，按迁移原注释回退定位）。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from app.config import get_settings

VERSION = "114_document_objects_deleted_after"
COLUMN = "deleted_after_ref"


def _database_path() -> Path:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise SystemExit("DATABASE_URL 必须是 sqlite:///...")
    return Path(url.removeprefix("sqlite:///"))


def _state(connection: sqlite3.Connection) -> tuple[bool, bool]:
    recorded = connection.execute(
        "select 1 from sqlite_master where type = 'table' and name = 'schema_migrations'"
    ).fetchone() is not None and connection.execute(
        "select 1 from schema_migrations where version = ?", (VERSION,)
    ).fetchone() is not None
    columns = {row[1] for row in connection.execute("pragma table_info(document_objects)")}
    return recorded, COLUMN in columns


def rollback(path: Path, apply: bool) -> int:
    if not path.exists():
        print(f"没有找到数据库：{path}", file=sys.stderr)
        return 1
    connection = sqlite3.connect(path, timeout=30)
    try:
        recorded, has_column = _state(connection)
        if not recorded and not has_column:
            print("迁移 114 未执行过，不需要回滚。")
            return 0
        filled = connection.execute(
            f"select count(*) from document_objects where {COLUMN} is not null"
        ).fetchone()[0] if has_column else 0
        print(f"数据库：{path}")
        print(f"迁移记录：{'有' if recorded else '无'}；{COLUMN} 列：{'有' if has_column else '无'}；其中有值的已删除对象 {filled} 个（回滚后丢失）。")
        if not apply:
            print("只做了检查。确认已停服务后加 --yes 执行回滚。")
            return 0
        backup = path.with_name(f"{path.name}.before-rollback-114-{datetime.now():%Y%m%d-%H%M%S}.db")
        target = sqlite3.connect(backup)
        try:
            connection.backup(target)
        finally:
            target.close()
        print(f"已备份到：{backup}")
        connection.execute("BEGIN IMMEDIATE")
        try:
            if has_column:
                connection.execute(f"ALTER TABLE document_objects DROP COLUMN {COLUMN}")
            if recorded:
                connection.execute("delete from schema_migrations where version = ?", (VERSION,))
            connection.execute("COMMIT")
        except sqlite3.Error:
            connection.execute("ROLLBACK")
            raise
        print("已回滚迁移 114。")
        return 0
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="单独回滚迁移 114（document_objects.deleted_after_ref）")
    parser.add_argument("--yes", action="store_true", help="确认执行（默认只检查）")
    args = parser.parse_args(argv)
    return rollback(_database_path(), args.yes)


if __name__ == "__main__":
    sys.exit(main())
