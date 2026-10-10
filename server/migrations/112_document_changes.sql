-- 阶段2 S4：修改记录、逐对象变更、版本操作人（01 §3.3、§4.5、§4.6）。
CREATE TABLE document_changes (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  doc_revision INTEGER NOT NULL,
  base_revision INTEGER NOT NULL,
  actor_id TEXT REFERENCES users(id),                   -- 回填/重新对齐记录为 NULL
  kind TEXT NOT NULL,
  ai_assisted INTEGER NOT NULL DEFAULT 0 CHECK (ai_assisted IN (0,1)),
  merged INTEGER NOT NULL DEFAULT 0 CHECK (merged IN (0,1)),
  client_save_key TEXT,
  request_hash TEXT,
  result_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX idx_document_changes_revision ON document_changes(document_id, doc_revision) WHERE kind NOT IN ('backfill','realign');
CREATE UNIQUE INDEX idx_document_changes_save_key
  ON document_changes(document_id, actor_id, client_save_key) WHERE client_save_key IS NOT NULL;
CREATE INDEX idx_document_changes_actor ON document_changes(document_id, actor_id, created_at);

CREATE TABLE document_object_changes (
  change_id TEXT NOT NULL REFERENCES document_changes(id) ON DELETE CASCADE,
  object_id TEXT NOT NULL,
  op TEXT NOT NULL CHECK (op IN ('insert','update','delete','move')),
  before_revision INTEGER, after_revision INTEGER,
  before_body TEXT, after_body TEXT,
  location_json TEXT,                                   -- 4.9 位置；delete 为删除前位置
  PRIMARY KEY (change_id, object_id)
);
CREATE INDEX idx_document_object_changes_object ON document_object_changes(object_id);

ALTER TABLE document_versions ADD COLUMN created_by TEXT;              -- 不加 REFERENCES：SQLite 不能 DROP 带外键的列（down 脚本需要）
ALTER TABLE document_versions ADD COLUMN changed_objects INTEGER;
ALTER TABLE documents ADD COLUMN updated_by TEXT;                      -- 同上
