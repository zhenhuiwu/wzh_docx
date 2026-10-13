-- 阶段2 S4：内容对象（01 §3.3）。documents.markdown_content 仍为物化全文；对象由代码惰性回填。
ALTER TABLE documents ADD COLUMN objects_revision INTEGER;            -- NULL = 尚未建立对象
ALTER TABLE documents ADD COLUMN objects_prefix TEXT NOT NULL DEFAULT '';

CREATE TABLE document_objects (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('heading','paragraph','list_item','table','image','code','other')),
  heading_level INTEGER CHECK (heading_level BETWEEN 1 AND 6),
  position INTEGER NOT NULL,
  body TEXT NOT NULL,                                   -- 不叫 content（db.py 会按 JSON 解码）
  gap_after TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
  created_doc_revision INTEGER NOT NULL,
  updated_doc_revision INTEGER NOT NULL,
  created_by TEXT REFERENCES users(id),
  updated_by TEXT REFERENCES users(id),
  origin_object_id TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TEXT,
  deleted_doc_revision INTEGER
);
CREATE INDEX idx_document_objects_live ON document_objects(document_id, position) WHERE deleted_at IS NULL;
CREATE INDEX idx_document_objects_changed ON document_objects(document_id, updated_doc_revision);
