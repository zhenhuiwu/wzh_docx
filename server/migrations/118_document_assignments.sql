-- A07 段落 / 章节负责人（硬限制，17 §4.5、§4.9、§10.4、§10.7）。
-- 只存显式分配；归属在读取时沿“对象自身 → 最近的标题链 → 文档级 '@document' → 未分配”计算，不落库。
-- 回滚：python -m app.rollback_migration_118（有分配或被拒保存时需 --force；先导出 CSV、整库备份，再 DROP 表 / 列）。
CREATE TABLE document_object_assignments (
  document_id TEXT NOT NULL REFERENCES documents(id),
  object_id TEXT NOT NULL,                       -- document_objects.id，或保留值 '@document'（整篇默认负责人）
  assignee_id TEXT NOT NULL REFERENCES users(id),
  source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','split','creator')),
  needs_confirm INTEGER NOT NULL DEFAULT 0 CHECK (needs_confirm IN (0,1)),
  assigned_by TEXT,
  assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (document_id, object_id, assignee_id)
);
CREATE INDEX idx_assign_assignee ON document_object_assignments(assignee_id);
CREATE INDEX idx_assign_object ON document_object_assignments(document_id, object_id);

-- 分工模式：已有文档 open（U-14 / H-9），由负责人开启；新建文档由 create_document 显式写 enforced。
-- 列默认值取 open：不经新建接口直接写库的旧路径（导入脚本等）保持旧行为，不会让编写人员突然只读。
ALTER TABLE documents ADD COLUMN assignment_mode TEXT NOT NULL DEFAULT 'open'
  CHECK (assignment_mode IN ('open','enforced'));
ALTER TABLE documents ADD COLUMN assignments_revision INTEGER NOT NULL DEFAULT 0;

-- 被拒提交保留（字不丢）：每人每文档最近 5 份、30 天；最新一份在本人处理前不因份数清理（17 §10.7）
CREATE TABLE document_rejected_saves (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id),
  actor_id TEXT NOT NULL REFERENCES users(id),
  base_revision INTEGER NOT NULL,
  markdown TEXT NOT NULL,
  block_ids TEXT,
  violations TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_rejected_saves ON document_rejected_saves(document_id, actor_id, created_at DESC);

-- 分工事件（版本记录 / 审计）：assign / unassign / mode_on / mode_off / cleared / lost；被拒提交 assignment_violation
CREATE TABLE document_annotation_events (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id),
  kind TEXT NOT NULL CHECK (kind IN ('assignment','assignment_violation')),
  action TEXT NOT NULL,
  object_ids TEXT NOT NULL DEFAULT '[]',
  subject_user_id TEXT,
  payload TEXT NOT NULL DEFAULT '{}',
  actor_id TEXT,
  doc_revision INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_annot_doc ON document_annotation_events(document_id, created_at DESC);
CREATE INDEX idx_annot_subject ON document_annotation_events(subject_user_id, kind);
