CREATE INDEX IF NOT EXISTS idx_document_memory_target
ON document_memory_events(document_id, scope, target, revision);
