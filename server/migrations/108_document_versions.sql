CREATE TABLE document_versions (
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  revision INTEGER NOT NULL CHECK (revision >= 0),
  markdown_content TEXT NOT NULL,
  outline TEXT NOT NULL,
  format_template TEXT NOT NULL,
  memory_revisions TEXT NOT NULL,
  status TEXT NOT NULL,
  kind TEXT NOT NULL,
  restored_from_revision INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (document_id, revision)
);
