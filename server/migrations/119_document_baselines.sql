-- 文档版本（git 式快照，章节内容哈希去重）与项目基线（30 §3.5）。
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
-- 项目基线（30 §3.5 的“基线”= git tag）：选若干文档，各记一个文档版本。
CREATE TABLE IF NOT EXISTS project_baselines (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  name TEXT NOT NULL,
  author_id TEXT REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (project_id, seq)
);
CREATE TABLE IF NOT EXISTS project_baseline_items (
  project_baseline_id TEXT NOT NULL REFERENCES project_baselines(id) ON DELETE CASCADE,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  document_baseline_id TEXT NOT NULL REFERENCES document_baselines(id),
  PRIMARY KEY (project_baseline_id, document_id)
);
