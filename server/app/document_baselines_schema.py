"""文档基线（git 式快照）表结构。

迁移编号待 A07 合入后再定（见 PR 说明），这里只保留 DDL，正式迁移文件
`migrations/1NN_document_baselines.sql` 的内容即 BASELINE_SCHEMA_SQL。
测试通过 apply_baseline_schema() 在临时库上建表。

存储：章节正文按 sha256 去重存进 document_baseline_blobs，同一章节在多次基线间
只存一份；document_baseline_sections 只记 (基线, 位置, 哈希, 标题)。几百页的
文档建基线时只写入有变化的章节正文。
"""

from __future__ import annotations

BASELINE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS document_baseline_blobs (
  hash TEXT PRIMARY KEY,
  body TEXT NOT NULL,
  paragraph_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS document_baselines (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  message TEXT NOT NULL DEFAULT '',
  author_id TEXT REFERENCES users(id),
  kind TEXT NOT NULL DEFAULT 'create' CHECK (kind IN ('create','revert')),
  reverted_from_seq INTEGER,
  revision INTEGER NOT NULL,
  format_template TEXT NOT NULL DEFAULT '{}',
  outline TEXT NOT NULL DEFAULT '[]',
  section_count INTEGER NOT NULL DEFAULT 0,
  paragraph_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (document_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_document_baselines_doc ON document_baselines(document_id, seq DESC);
CREATE TABLE IF NOT EXISTS document_baseline_sections (
  baseline_id TEXT NOT NULL REFERENCES document_baselines(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  hash TEXT NOT NULL REFERENCES document_baseline_blobs(hash),
  heading TEXT NOT NULL DEFAULT '',
  level INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (baseline_id, position)
);
CREATE INDEX IF NOT EXISTS idx_document_baseline_sections_hash ON document_baseline_sections(hash);
"""


def apply_baseline_schema(connection) -> None:
    connection._conn.executescript(BASELINE_SCHEMA_SQL)
