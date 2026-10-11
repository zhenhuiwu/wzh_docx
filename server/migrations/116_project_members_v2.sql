-- A05 项目成员与权限（17 §2.2）：成员独立版本号、成员行时间与操作人、成员变更审计。
-- ALTER TABLE ADD COLUMN 不能写 IF NOT EXISTS：只执行一次由 schema_migrations 保证，整个文件在一个事务里，失败整体回滚。
-- 回滚：python -m app.rollback_migration_116（先导出审计 CSV，再 DROP 表、索引和列）。

-- 1) 成员独立版本号：成员变更不再加 projects.revision（与“编辑项目资料”表单互不 409）
ALTER TABLE projects ADD COLUMN members_revision INTEGER NOT NULL DEFAULT 1;

-- 2) 成员行时间与操作人；旧数据为 NULL，接口返回 null，界面显示“—”（不伪造）
ALTER TABLE project_members ADD COLUMN created_at TEXT;
ALTER TABLE project_members ADD COLUMN updated_at TEXT;
ALTER TABLE project_members ADD COLUMN updated_by TEXT;          -- 不加 FK，便于回滚 DROP COLUMN

-- 3) 成员变更审计（不回填历史）
CREATE TABLE project_member_events (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  actor_id TEXT REFERENCES users(id),          -- 系统动作（如 owner_id 改指）可为 NULL
  target_user_id TEXT NOT NULL REFERENCES users(id),
  action TEXT NOT NULL CHECK (action IN ('add','change_role','remove','owner_reassigned')),
  old_role TEXT,
  new_role TEXT,
  members_revision INTEGER NOT NULL,           -- 变更后的成员版本号
  request_id TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_project_member_events_project ON project_member_events(project_id, created_at DESC);
CREATE INDEX idx_project_members_user ON project_members(user_id);   -- 停用账号时按人反查项目
