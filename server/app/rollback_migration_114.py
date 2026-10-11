"""单独回滚迁移 114（document_objects.deleted_after_ref）。

只在需要退回到 114 之前的程序版本时使用；**先停服务**，用与服务相同的 DATABASE_URL 运行::

    uv run python -m app.rollback_migration_114          # 只检查，不改库
    uv run python -m app.rollback_migration_114 --yes    # 先整库备份，再回滚（会要求输入 yes 确认已停服务）
    uv run python -m app.rollback_migration_114 --yes --service-stopped   # 非交互运行时

回滚做两件事（同一事务）：删掉 document_objects.deleted_after_ref 列、删掉 schema_migrations 里的 114 记录。
注意：
- 回滚必须从最新的迁移倒着来（… → 116 → 115 → 114）。库里还有 114 之后的迁移（115、116 或更新的，
  看 schema_migrations 记录和这些迁移建出的表/列）时本脚本拒绝执行（退出码 2），提示先回滚哪一个；
  只回滚 114 而保留 115 时，115 之后的程序仍会启动时重新执行 114。
- 该列记录“删除时紧挨在前面的对象”，回滚后这些位置信息丢失；已删除段落“恢复为新段落”回退到章节路径 + 序号定位（与 114 之前一致）。
- 备份文件与库同目录，名为 <库名>.before-rollback-114-<时间>.db；需要撤销回滚时停服务后用它覆盖原库即可。
- **回滚后必须用 114 之前的旧版本程序启动服务。** 若仍启动含 114 的新版本，服务启动时会自动重新执行 114
  （列重新加上，旧删除记录的该列为 NULL），回滚等于白做。
- 服务在运行时脚本拒绝回滚（退出码 2）：正在运行的新版本程序仍会写这一列，回滚后删段保存会出错。
- 能可靠识别的只有带运行标记的版本（ed68f33 及之后）：服务启动时持有 <库名>.service.lock 的锁，拿不到就拒绝。
- 以下两项只是辅助，**识别不了空闲的服务**：Linux 上查其他进程是否打开着库文件（服务按请求开关连接，空闲时不占用库文件）；
  尝试拿库的独占写锁（WAL 库空闲时能拿到）。
- 因此 114 之前的旧版本、以及 114 之后到 ed68f33 之前的中间版本（会写这一列却没有运行标记）在运行时**查不出来**，
  必须人工确认所有使用该库的服务都已停止：--yes 时会要求输入 yes 确认，非交互运行须另加 --service-stopped。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from app import service_lock
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


# 已知迁移留下的结构（schema_migrations 记录被手工删掉时仍能认出来）。新增可回滚迁移时在这里登记。
LATER_ARTIFACTS: dict[int, tuple[tuple[str, ...], ...]] = {
    115: (("table", "document_conflict_mine_snapshots"),),
    116: (("table", "project_member_events"), ("column", "projects", "members_revision")),
}


def later_migrations(connection: sqlite3.Connection, number: int) -> list[str]:
    """库里仍在的、编号大于 number 的迁移（倒序：最新的在前）。回滚 number 之前必须先把它们依次回滚。

    两种来源取并集：schema_migrations 里编号更大的记录（通用，也覆盖本分支还不知道的更新迁移），
    以及 LATER_ARTIFACTS 登记的表/列（记录被删但结构还在的情况）。"""
    found: dict[int, str] = {}
    if connection.execute("select 1 from sqlite_master where type = 'table' and name = 'schema_migrations'").fetchone():
        for (version,) in connection.execute("select version from schema_migrations"):
            prefix = str(version).split("_", 1)[0]
            if prefix.isdigit() and int(prefix) > number:
                found[int(prefix)] = str(version)
    for later, artifacts in LATER_ARTIFACTS.items():
        if later <= number or later in found:
            continue
        for artifact in artifacts:
            if artifact[0] == "table":
                present = connection.execute(
                    "select 1 from sqlite_master where type = 'table' and name = ?", (artifact[1],)).fetchone() is not None
            else:
                present = artifact[2] in {row[1] for row in connection.execute(f"pragma table_info({artifact[1]})")}
            if present:
                found[later] = f"{later}（{artifact[-1]}）"
                break
    return [found[key] for key in sorted(found, reverse=True)]


def refuse_if_later(connection: sqlite3.Connection, number: int) -> bool:
    """有更新的迁移还在时打印原因并返回 True（调用方以退出码 2 结束，库不变）。"""
    later = later_migrations(connection, number)
    if not later:
        return False
    newest = later[0].split("_", 1)[0].split("（", 1)[0]
    order = " → ".join([item.split("_", 1)[0].split("（", 1)[0] for item in later] + [str(number)])
    print(f"不能回滚迁移 {number}：更新的迁移还在（{'、'.join(later)}）。"
          f"请先运行 python -m app.rollback_migration_{newest}，按 {order} 的顺序依次回滚。", file=sys.stderr)
    return True


def _in_use(path: Path, connection: sqlite3.Connection) -> str:
    """返回占用原因；空串表示没发现服务在用这个库。"""
    if service_lock.service_running(path):
        return f"运行标记 {service_lock.lock_path(path).name} 被占用"
    others = service_lock.processes_with_file(path)
    if others:
        return f"进程 {', '.join(map(str, others))} 打开着该库"
    try:
        connection.execute("pragma busy_timeout = 0")
        connection.execute("BEGIN EXCLUSIVE")
        connection.execute("ROLLBACK")
    except sqlite3.OperationalError:
        return "库正被其他连接锁定"
    finally:
        connection.execute("pragma busy_timeout = 30000")
    return ""


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
        # 测试工程师：115/116（或更新的迁移）还在时不能直接回滚 114——旧程序不认识它们，必须倒序回滚
        if refuse_if_later(connection, 114):
            return 2
        print(f"数据库：{path}")
        print(f"迁移记录：{'有' if recorded else '无'}；{COLUMN} 列：{'有' if has_column else '无'}；其中有值的已删除对象 {filled} 个（回滚后丢失）。")
        busy = _in_use(path, connection)
        if busy:
            print(f"服务似乎正在运行（{busy}）。请先停止使用该库的服务，再执行回滚。", file=sys.stderr)
            if apply:
                return 2
        if not apply:
            print("只做了检查。确认已停服务后加 --yes 执行回滚；回滚后必须用 114 之前的旧版本程序启动，否则会重新执行 114。")
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
        print("已回滚迁移 114。现在请用 114 之前的旧版本程序启动服务（新版本启动会重新执行 114）。")
        return 0
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="单独回滚迁移 114（document_objects.deleted_after_ref）")
    parser.add_argument("--yes", action="store_true", help="确认执行（默认只检查）")
    parser.add_argument("--service-stopped", action="store_true", help="非交互运行时声明：已人工确认所有使用该库的服务都已停止")
    args = parser.parse_args(argv)
    path = _database_path()
    if args.yes and path.exists():
        # 终验可顺手做：能检测到服务在运行时，在要求输入 yes 之前就拒绝（不让人白确认一次）
        connection = sqlite3.connect(path, timeout=30)
        try:
            busy = _in_use(path, connection)
        finally:
            connection.close()
        if busy:
            print(f"服务似乎正在运行（{busy}）。请先停止使用该库的服务，再执行回滚。", file=sys.stderr)
            return 2
    if args.yes and not args.service_stopped:
        # 旧版本/中间版本的服务没有运行标记、空闲时也查不到，必须人工确认
        prompt = "旧版本服务没有运行标记，空闲时检测不到。确认所有使用该库的服务都已停止，请输入 yes："
        if not sys.stdin.isatty():
            print("非交互运行时请在人工确认服务已停止后加 --service-stopped。", file=sys.stderr)
            return 2
        if input(prompt).strip().lower() != "yes":
            print("已取消，库未改动。")
            return 2
    return rollback(path, args.yes)


if __name__ == "__main__":
    sys.exit(main())
