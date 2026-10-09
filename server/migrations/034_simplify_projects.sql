-- 简化项目表：移除不需要的字段

ALTER TABLE projects DROP COLUMN IF EXISTS organization;
ALTER TABLE projects DROP COLUMN IF EXISTS department;
ALTER TABLE projects DROP COLUMN IF EXISTS group_name;
ALTER TABLE projects DROP COLUMN IF EXISTS period_start;
ALTER TABLE projects DROP COLUMN IF EXISTS period_end;
ALTER TABLE projects DROP COLUMN IF EXISTS software_version;
ALTER TABLE projects DROP COLUMN IF EXISTS archived_at;
