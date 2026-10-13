ALTER TABLE documents ADD COLUMN assignments_enabled INTEGER NOT NULL DEFAULT 0 CHECK (assignments_enabled IN (0,1));
ALTER TABLE documents ADD COLUMN assignments_revision INTEGER NOT NULL DEFAULT 0;

CREATE TABLE document_object_assignments (
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  object_id TEXT NOT NULL REFERENCES document_objects(id) ON DELETE CASCADE,
  assignee_id TEXT NOT NULL REFERENCES users(id),
  updated_by TEXT REFERENCES users(id),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (document_id, object_id)
);
CREATE INDEX idx_object_assignments_user ON document_object_assignments(assignee_id, document_id);
CREATE TABLE document_assignment_events (
  id INTEGER PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  assignments_revision INTEGER NOT NULL,
  actor_id TEXT REFERENCES users(id),
  changes_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
