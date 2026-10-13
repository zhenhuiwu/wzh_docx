-- 任务与模板：下游“上游有新基线”改按项目基线判断；记录下游已核对到的上游项目基线号（30 §3.5“标记为已核对”）。
-- 回滚：python -m app.rollback_migration_122（DROP COLUMN）。
ALTER TABLE document_upstreams ADD COLUMN acked_baseline_seq INTEGER;
