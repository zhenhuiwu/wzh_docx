-- 阶段2 方案 C：冲突后在编辑区继续写的“我的版本”，只追加快照（不改 document_conflicts.payload_json 里的原始内容）。
-- 处理（保留我的/都保留/恢复为新段落）用同一条目最新的一份快照；client_save_key 保证同一份快照重发不重复写。
CREATE TABLE document_conflict_mine_snapshots (
  id TEXT PRIMARY KEY,
  conflict_id TEXT NOT NULL REFERENCES document_conflicts(id) ON DELETE CASCADE,
  item_index INTEGER NOT NULL,
  actor_id TEXT NOT NULL REFERENCES users(id),
  markdown TEXT NOT NULL,
  client_save_key TEXT NOT NULL,
  tab_id TEXT,
  source TEXT NOT NULL DEFAULT 'editor' CHECK (source IN ('editor','resubmit','draft')),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE UNIQUE INDEX idx_conflict_mine_snapshots_key ON document_conflict_mine_snapshots(conflict_id, client_save_key);
CREATE INDEX idx_conflict_mine_snapshots_item ON document_conflict_mine_snapshots(conflict_id, item_index);
