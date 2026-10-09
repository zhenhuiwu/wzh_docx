-- Undo invalidates acceptance while preserving the original writing request.
CREATE TABLE document_memory_retractions (
  document_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK (revision >= 0),
  retracted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (document_id, revision),
  FOREIGN KEY (document_id, revision)
    REFERENCES document_memory_events(document_id, revision) ON DELETE CASCADE
);
