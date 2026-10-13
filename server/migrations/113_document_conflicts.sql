-- 阶段2 S4：冲突副本（01 §3.3、§4.4.1）。payload_json 中“我的内容”永不修改、永不删除（决策5）。
CREATE TABLE document_conflicts (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  actor_id TEXT NOT NULL REFERENCES users(id),
  base_revision INTEGER NOT NULL,
  doc_revision INTEGER NOT NULL,
  source TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','discarded')),
  resolved_change_ref TEXT REFERENCES document_changes(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at TEXT
);
CREATE INDEX idx_document_conflicts_open ON document_conflicts(document_id, actor_id) WHERE status = 'open';
CREATE UNIQUE INDEX idx_document_conflicts_dedupe ON document_conflicts(document_id, actor_id, request_hash) WHERE status = 'open';

CREATE TABLE document_conflict_items (
  conflict_id TEXT NOT NULL REFERENCES document_conflicts(id) ON DELETE CASCADE,
  item_index INTEGER NOT NULL,
  reason TEXT NOT NULL CHECK (reason IN ('MODIFIED','DELETED','ANCHOR_LOST','STRUCTURE','FORMAT')),
  object_ref TEXT,
  location_json TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','discarded')),
  action TEXT,
  resolution_key TEXT,
  resolved_change_ref TEXT REFERENCES document_changes(id),
  resolved_by TEXT REFERENCES users(id),
  resolved_at TEXT,
  PRIMARY KEY (conflict_id, item_index)
);
CREATE INDEX idx_conflict_items_open ON document_conflict_items(status, conflict_id);
