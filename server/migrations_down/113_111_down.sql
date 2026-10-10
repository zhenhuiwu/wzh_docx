-- 仅供人工结构回滚（01 §3.5）。不要放进 migrations/（会被当作正向迁移执行）。
-- 先用 SQLite backup API 备份库；110 的 owner 成员补齐不回滚。同时撤销 114（列随 document_objects 表一起删除）。
BEGIN;
DROP TABLE IF EXISTS document_conflict_items;
DROP TABLE IF EXISTS document_conflicts;
DROP TABLE IF EXISTS document_object_changes;
DROP TABLE IF EXISTS document_changes;
DROP TABLE IF EXISTS document_objects;
ALTER TABLE documents DROP COLUMN updated_by;
ALTER TABLE documents DROP COLUMN objects_prefix;
ALTER TABLE documents DROP COLUMN objects_revision;
ALTER TABLE document_versions DROP COLUMN changed_objects;
ALTER TABLE document_versions DROP COLUMN created_by;
DELETE FROM schema_migrations WHERE version IN ('111_document_objects', '112_document_changes', '113_document_conflicts',
                                             '114_document_objects_deleted_after');
COMMIT;
