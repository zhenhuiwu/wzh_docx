"""单独回滚迁移 117（template_versions：A06 模板版本快照）。

只在需要退回到 117 之前的程序版本时使用；**先停服务**，用与服务相同的 DATABASE_URL 运行::

    uv run python -m app.rollback_migration_117          # 只检查，不改库
    uv run python -m app.rollback_migration_117 --yes    # 先导出版本表 CSV、整库备份，再回滚（会要求输入 yes 确认已停服务）
    uv run python -m app.rollback_migration_117 --yes --service-stopped   # 非交互运行时
    uv run python -m app.rollback_migration_117 --yes --force            # 已有第 2 版及以后、或在线编辑过的版本时必须加

回滚在同一事务里做这些事（17 §3.4“回滚”）：
- 把每个模板文档**当前已发布版本**写回 template_documents（旧程序只认这里）：
  - 只有第 1 版且未编辑：importStructure 本来就是第 1 版，不动；
  - 有编辑后的版本：word_file_id 改指向该版本的 Word（edited，没有则 source），importStructure 按这份 Word 重新解析，
    并按段落位置把该版本的分类、标题级别、编写指导和绑定带过去（旧程序的块 ID 由文件哈希和位置决定，所以 ID 会变）；
    只存在于结构里的“虚拟指导”（在线新增、或随删除章节保留的指导）旧程序无处安放，**会丢失**（已导出在 CSV 里）；
- 删掉 template_document_versions 表（连同索引）、idx_documents_template_version、idx_files_sha256_purpose；
- ALTER TABLE … DROP COLUMN：template_documents.current_version_id、documents.template_version_id；
- 删掉 schema_migrations 里的 117 记录。
注意：
- 已有第 2 版及以后或在线编辑过的版本时默认拒绝（版本历史、发布人、变更说明会丢失），确认后加 --force；
- 版本表整表导出为 CSV（与库同目录，<库名>.template_document_versions-117-<时间>.csv），再整库备份为
  <库名>.before-rollback-117-<时间>.db；需要撤销回滚时停服务后用它覆盖原库即可；
- 生成的 Word 文件保留在 files 里（不删文件）；已有文档的结构拷贝不受影响；
- **回滚后必须用 117 之前的旧版本程序启动服务**，否则启动时会自动重新执行 117 并回填第 1 版；
- 服务在运行时脚本拒绝回滚（退出码 2）；能否检测到运行中的服务与 rollback_migration_114 相同（见该文件说明）。
回滚顺序：117 → 116 → 115 → 114。
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from app.rollback_migration_114 import _database_path, _in_use, refuse_if_later

VERSION = "117_template_versions"
TABLE = "template_document_versions"
INDEXES = ("idx_documents_template_version", "idx_files_sha256_purpose")
COLUMNS = (("template_documents", "current_version_id"), ("documents", "template_version_id"))


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"pragma table_info({table})")}


def _state(connection: sqlite3.Connection) -> dict[str, object]:
    recorded = connection.execute(
        "select 1 from sqlite_master where type = 'table' and name = 'schema_migrations'"
    ).fetchone() is not None and connection.execute(
        "select 1 from schema_migrations where version = ?", (VERSION,)
    ).fetchone() is not None
    has_table = connection.execute("select 1 from sqlite_master where type = 'table' and name = ?", (TABLE,)).fetchone() is not None
    indexes = [name for name in INDEXES
               if connection.execute("select 1 from sqlite_master where type = 'index' and name = ?", (name,)).fetchone() is not None]
    columns = [(table, column) for table, column in COLUMNS if column in _columns(connection, table)]
    return {"recorded": recorded, "table": has_table, "indexes": indexes, "columns": columns}


def _export(connection: sqlite3.Connection, path: Path) -> tuple[Path, int]:
    target = path.with_name(f"{path.name}.{TABLE}-117-{datetime.now():%Y%m%d-%H%M%S}.csv")
    cursor = connection.execute(f"select * from {TABLE} order by template_document_id, coalesce(version, 1000000), created_at")
    names = [column[0] for column in cursor.description]
    count = 0
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(names)
        for row in cursor:
            writer.writerow(row)
            count += 1
    return target, count


def legacy_structure(word: Path, published: dict, revision: int) -> dict:
    """把已发布版本的结构换算成 117 之前程序能认的 importStructure（块 ID 按文件哈希 + 位置重新生成）。"""
    from app.template_import import parse_template

    parsed = parse_template(word)
    by_path = {block["path"]: block for block in published["blocks"] if block.get("path")}
    id_map = {by_path[block["path"]]["id"]: block["id"] for block in parsed["blocks"] if block["path"] in by_path}
    current = None
    for block in parsed["blocks"]:
        source = by_path.get(block["path"])
        if source is not None:
            block["kind"] = source["kind"]
            block["headingLevel"] = source["headingLevel"] if source["kind"] == "heading" else None
            if source["kind"] == "guidance":
                block["text"] = source["text"]
                block["headingId"] = id_map.get(source.get("headingId"))
        if block["kind"] == "heading":
            current = block["id"]
            block["headingId"] = None
        elif block["kind"] != "guidance" or block["headingId"] is None:
            block["headingId"] = current
    if any(block["kind"] == "guidance" and block["headingId"] is None for block in parsed["blocks"]):
        first = next((block["id"] for block in parsed["blocks"] if block["kind"] == "heading"), None)
        for block in parsed["blocks"]:
            if block["kind"] == "guidance" and block["headingId"] is None:
                block["headingId"] = first
    parsed["reviewed"] = True
    parsed["revision"] = revision
    return parsed


def _write_back(connection: sqlite3.Connection) -> list[str]:
    from app.config import get_settings
    from app.files import resolve_object

    changed = []
    rows = connection.execute(f"""select td.id, td.format_template, td.word_file_id, v.version, v.source_file_id, v.edited_file_id,
                                         v.structure_json, f.storage_key
                                  from template_documents td join {TABLE} v on v.id = td.current_version_id
                                  join files f on f.id = coalesce(v.edited_file_id, v.source_file_id)""").fetchall()
    for td_id, format_template, word_file_id, version, source_id, edited_id, structure_json, storage_key in rows:
        if version == 1 and edited_id is None:
            continue
        template = json.loads(format_template or "{}")
        word = resolve_object(get_settings().file_data_dir, storage_key)
        template["importStructure"] = legacy_structure(word, json.loads(structure_json), version)
        connection.execute("update template_documents set format_template = ?, word_file_id = ? where id = ?",
                           (json.dumps(template, ensure_ascii=False), edited_id or source_id, td_id))
        changed.append(td_id)
    return changed


def rollback(path: Path, apply: bool, force: bool = False) -> int:
    if not path.exists():
        print(f"没有找到数据库：{path}", file=sys.stderr)
        return 1
    connection = sqlite3.connect(path, timeout=30)
    try:
        state = _state(connection)
        if not (state["recorded"] or state["table"] or state["indexes"] or state["columns"]):
            print("迁移 117 未执行过，不需要回滚。")
            return 0
        if refuse_if_later(connection, 117):
            return 2
        versions = edited = drafts = 0
        if state["table"]:
            versions = connection.execute(f"select count(*) from {TABLE} where status <> 'draft'").fetchone()[0]
            edited = connection.execute(f"select count(*) from {TABLE} where status <> 'draft' and (version > 1 or edited_file_id is not null)").fetchone()[0]
            drafts = connection.execute(f"select count(*) from {TABLE} where status = 'draft' and parent_version_id is not null").fetchone()[0]
        print(f"数据库：{path}")
        print(f"迁移记录：{'有' if state['recorded'] else '无'}；{TABLE} 表：{'有' if state['table'] else '无'}"
              f"（{versions} 个已发布/历史版本，其中第 2 版及以后或编辑过的 {edited} 个；未发布的编辑草稿 {drafts} 份，回滚后丢失）。")
        busy = _in_use(path, connection)
        if busy:
            print(f"服务似乎正在运行（{busy}）。请先停止使用该库的服务，再执行回滚。", file=sys.stderr)
            if apply:
                return 2
        if edited and not force:
            print("已有第 2 版及以后或在线编辑过的模板版本：回滚会丢失版本历史、发布人和变更说明，"
                  "当前已发布的结构会写回模板（虚拟编写指导会丢失）。确认后加 --force。", file=sys.stderr)
            if apply:
                return 3
        if not apply:
            print("只做了检查。确认已停服务后加 --yes 执行回滚；回滚后必须用 117 之前的旧版本程序启动，否则会重新执行 117。")
            return 0
        if state["table"]:
            exported, count = _export(connection, path)
            print(f"已导出 {count} 行模板版本到：{exported}")
        backup = path.with_name(f"{path.name}.before-rollback-117-{datetime.now():%Y%m%d-%H%M%S}.db")
        target = sqlite3.connect(backup)
        try:
            connection.backup(target)
        finally:
            target.close()
        print(f"已备份到：{backup}")
        connection.execute("BEGIN IMMEDIATE")
        try:
            if state["table"] and ("template_documents", "current_version_id") in state["columns"]:
                changed = _write_back(connection)
                print(f"已把 {len(changed)} 个模板文档的当前已发布版本写回模板。")
            for name in state["indexes"]:
                connection.execute(f"DROP INDEX {name}")
            if state["table"]:
                connection.execute(f"DROP TABLE {TABLE}")
            for table, column in state["columns"]:
                connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
            if state["recorded"]:
                connection.execute("delete from schema_migrations where version = ?", (VERSION,))
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        print("已回滚迁移 117。现在请用 117 之前的旧版本程序启动服务（新版本启动会重新执行 117）。")
        return 0
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="单独回滚迁移 117（模板版本快照）")
    parser.add_argument("--yes", action="store_true", help="确认执行（默认只检查）")
    parser.add_argument("--force", action="store_true", help="已有第 2 版及以后或编辑过的版本时仍然回滚")
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
    return rollback(path, args.yes, args.force)


if __name__ == "__main__":
    sys.exit(main())
