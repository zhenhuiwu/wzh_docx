"""迁移 117 的数据部分（17 §3.4）：给模板文档补第 1 版 / 初始草稿，并把结构与第 1 版一致的老文档绑定到第 1 版。

随服务启动在 db.migrate() 之后自动执行，也可单独重跑（幂等）::

    uv run python -m app.migrate_template_versions

- 有需要回填的数据时，先用 SQLite backup API 整库备份到 <库目录>/backups/app-pre117-<时间>.db（C13）；没有要做的事就不备份；
- 每个模板文档一个 IMMEDIATE 事务；条件是 current_version_id IS NULL / template_version_id IS NULL，重跑不会重复；
- 不为历史结构伪造版本：结构与第 1 版不同的老文档保持 template_version_id = NULL（界面显示“版本化之前”）。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from app.db import connect, database_path, write_transaction

logger = logging.getLogger(__name__)


def _pending(conn) -> tuple[list, list]:
    templates = conn.execute("""SELECT td.id FROM template_documents td
                                WHERE td.current_version_id IS NULL
                                  AND NOT EXISTS (SELECT 1 FROM template_document_versions v
                                                  WHERE v.template_document_id = td.id AND v.status = 'draft')
                                ORDER BY td.created_at""").fetchall()
    documents = conn.execute("""SELECT DISTINCT d.template_document_id AS td_id FROM documents d
                                JOIN template_documents td ON td.id = d.template_document_id
                                WHERE d.template_version_id IS NULL AND td.current_version_id IS NOT NULL""").fetchall()
    return [row["id"] for row in templates], [row["td_id"] for row in documents]


def _backup() -> str | None:
    source = database_path()
    if not source.exists():
        return None
    folder = source.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"app-pre117-{datetime.now():%Y%m%d-%H%M%S-%f}.db"
    with sqlite3.connect(source, timeout=30) as origin, sqlite3.connect(target) as copy:
        origin.backup(copy)
    return str(target)


def backfill(backup: bool = True) -> dict[str, object]:
    from app.template_versions import bind_documents, ensure_v1

    report: dict[str, object] = {"templateDocuments": 0, "published": 0, "drafts": 0, "bound": 0, "legacy": 0, "failed": [], "backup": None}
    with connect() as conn:
        templates, documents = _pending(conn)
        if not templates and not documents:
            return report
        if backup:
            report["backup"] = _backup()
        for td_id in templates:
            report["templateDocuments"] += 1
            try:
                with write_transaction(conn):
                    version = ensure_v1(conn, td_id, bind=False)
            except Exception as error:  # 单个模板文档失败不影响其他（例如 Word 文件丢失）
                report["failed"].append({"templateDocumentId": str(td_id), "error": getattr(error, "message", str(error))})
                continue
            if version is None:
                report["drafts"] += 1
            else:
                report["published"] += 1
        for td_id in dict.fromkeys([*documents, *(t for t in templates)]):
            row = conn.execute("""SELECT v.id, v.structure_sha256 FROM template_document_versions v
                                  WHERE v.template_document_id=%s AND v.version=1""", [td_id]).fetchone()
            if row is None:
                continue
            with write_transaction(conn):
                bound, legacy = bind_documents(conn, td_id, row["id"], row["structure_sha256"])
            report["bound"] += bound
            report["legacy"] += legacy
    if report["templateDocuments"] or report["bound"]:
        logger.info("template versions backfill: %s", report)
    return report


def main() -> None:
    from app.db import migrate

    migrate()
    report = backfill()
    print(f"模板文档 {report['templateDocuments']} 个：第 1 版 {report['published']}，初始草稿 {report['drafts']}；"
          f"文档绑定第 1 版 {report['bound']} 篇，版本化之前 {report['legacy']} 篇。")
    if report["backup"]:
        print(f"回填前备份：{report['backup']}")
    for item in report["failed"]:
        print(f"失败：{item['templateDocumentId']}：{item['error']}")


if __name__ == "__main__":
    main()
