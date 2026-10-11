"""单独回滚迁移 115（document_conflict_mine_snapshots：方案 C 冲突段续写的“我的版本”快照）。

只在需要退回到 115 之前的程序版本时使用；**先停服务**，用与服务相同的 DATABASE_URL 运行::

    uv run python -m app.rollback_migration_115          # 只检查，不改库
    uv run python -m app.rollback_migration_115 --yes    # 先整库备份，再回滚（会要求输入 yes 确认已停服务）
    uv run python -m app.rollback_migration_115 --yes --service-stopped   # 非交互运行时

回滚做两件事（同一事务）：删掉 document_conflict_mine_snapshots 表、删掉 schema_migrations 里的 115 记录。
注意：
- 快照表里是“冲突后继续写的内容”。回滚后未处理冲突只剩冲突发生时的原始“我的版本”（payload_json 不受影响），
  续写部分只在备份库里。回滚前建议让相关成员先处理完未处理的冲突；检查模式会列出仍未处理、带续写快照的条目数。
- 回滚顺序：116 → 115 → 114。库里还有 116（或更新的迁移）时本脚本拒绝执行（退出码 2），提示先回滚 116；
  要退到 114 之前时先回滚 115，再运行 rollback_migration_114。
- 备份文件与库同目录，名为 <库名>.before-rollback-115-<时间>.db；需要撤销回滚时停服务后用它覆盖原库即可。
- **回滚后必须用 115 之前的旧版本程序启动服务**，否则启动时会自动重新执行 115（表重新建出来但为空）。
- 服务在运行时脚本拒绝回滚（退出码 2）；能否检测到运行中的服务与 rollback_migration_114 相同（见该文件说明）。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from app.rollback_migration_114 import _database_path, _in_use, refuse_if_later

VERSION = "115_document_conflict_mine_snapshots"
TABLE = "document_conflict_mine_snapshots"


def _state(connection: sqlite3.Connection) -> tuple[bool, bool]:
    recorded = connection.execute(
        "select 1 from sqlite_master where type = 'table' and name = 'schema_migrations'"
    ).fetchone() is not None and connection.execute(
        "select 1 from schema_migrations where version = ?", (VERSION,)
    ).fetchone() is not None
    has_table = connection.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?", (TABLE,)
    ).fetchone() is not None
    return recorded, has_table


def rollback(path: Path, apply: bool) -> int:
    if not path.exists():
        print(f"没有找到数据库：{path}", file=sys.stderr)
        return 1
    connection = sqlite3.connect(path, timeout=30)
    try:
        recorded, has_table = _state(connection)
        if not recorded and not has_table:
            print("迁移 115 未执行过，不需要回滚。")
            return 0
        # 测试 20 M-1：116（或更新的迁移）还在时拒绝，避免库停在“116 在、115 不在”的中间态
        if refuse_if_later(connection, 115):
            return 2
        total = open_items = 0
        if has_table:
            total = connection.execute(f"select count(*) from {TABLE}").fetchone()[0]
            open_items = connection.execute(
                f"""select count(distinct s.conflict_id || ':' || s.item_index) from {TABLE} s
                    join document_conflict_items i on i.conflict_id = s.conflict_id and i.item_index = s.item_index
                    where i.status = 'open'""").fetchone()[0]
        print(f"数据库：{path}")
        print(f"迁移记录：{'有' if recorded else '无'}；{TABLE} 表：{'有' if has_table else '无'}；"
              f"快照 {total} 份，其中 {open_items} 处未处理冲突带续写（回滚后续写部分只在备份库里）。")
        busy = _in_use(path, connection)
        if busy:
            print(f"服务似乎正在运行（{busy}）。请先停止使用该库的服务，再执行回滚。", file=sys.stderr)
            if apply:
                return 2
        if not apply:
            print("只做了检查。确认已停服务后加 --yes 执行回滚；回滚后必须用 115 之前的旧版本程序启动，否则会重新执行 115。")
            return 0
        backup = path.with_name(f"{path.name}.before-rollback-115-{datetime.now():%Y%m%d-%H%M%S}.db")
        target = sqlite3.connect(backup)
        try:
            connection.backup(target)
        finally:
            target.close()
        print(f"已备份到：{backup}")
        connection.execute("BEGIN IMMEDIATE")
        try:
            if has_table:
                connection.execute(f"DROP TABLE {TABLE}")
            if recorded:
                connection.execute("delete from schema_migrations where version = ?", (VERSION,))
            connection.execute("COMMIT")
        except sqlite3.Error:
            connection.execute("ROLLBACK")
            raise
        print("已回滚迁移 115。现在请用 115 之前的旧版本程序启动服务（新版本启动会重新执行 115）。")
        return 0
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="单独回滚迁移 115（冲突“我的版本”快照表）")
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
