-- A06 模板版本快照（17 §3.2）：每个模板文档最多 1 份草稿、0 或 1 份已发布版本；已发布版本不可变。
-- 只建结构；回填（v1、文档绑定）由 app.migrate_template_versions 在 db.migrate() 之后执行（17 §3.4），可单独重跑。
-- 回滚：python -m app.rollback_migration_117（先把当前已发布结构写回 importStructure、导出版本表 CSV，再 DROP 表/索引/列）。
CREATE TABLE template_document_versions (
  id TEXT PRIMARY KEY,
  template_document_id TEXT NOT NULL REFERENCES template_documents(id) ON DELETE CASCADE,
  version INTEGER,                                   -- 发布时分配 max(version)+1；草稿为 NULL
  status TEXT NOT NULL CHECK (status IN ('draft','published','superseded')),
  source_file_id TEXT NOT NULL REFERENCES files(id), -- 原始上传 Word，永不修改
  source_sha256 TEXT NOT NULL,
  edited_file_id TEXT REFERENCES files(id),          -- 发布时生成的 Word；v1 且无编辑时为 NULL（用 source）
  edited_sha256 TEXT,
  structure_json TEXT NOT NULL,
  format_template_json TEXT NOT NULL,
  structure_sha256 TEXT NOT NULL,
  parent_version_id TEXT REFERENCES template_document_versions(id),
  restored_from_version_id TEXT REFERENCES template_document_versions(id),
  note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 200),
  revision INTEGER NOT NULL DEFAULT 1,
  created_by TEXT REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_by TEXT REFERENCES users(id),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  published_by TEXT REFERENCES users(id),
  published_at TEXT
);
CREATE UNIQUE INDEX ux_tdv_one_draft     ON template_document_versions(template_document_id) WHERE status = 'draft';
CREATE UNIQUE INDEX ux_tdv_one_published ON template_document_versions(template_document_id) WHERE status = 'published';
CREATE UNIQUE INDEX ux_tdv_version       ON template_document_versions(template_document_id, version) WHERE version IS NOT NULL;

ALTER TABLE template_documents ADD COLUMN current_version_id TEXT;   -- 不加 FK，便于回滚 DROP COLUMN
ALTER TABLE documents          ADD COLUMN template_version_id TEXT;  -- 同上；NULL = 早于版本化或无法确定（legacySnapshot）
CREATE INDEX idx_documents_template_version ON documents(template_version_id);
CREATE INDEX idx_files_sha256_purpose ON files(sha256, purpose);
