-- Only accepted writing requests enter document memory; original requests remain recoverable.
CREATE TABLE document_memory_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  revision INTEGER NOT NULL CHECK (revision >= 0),
  scope TEXT NOT NULL CHECK (scope IN ('section', 'selection', 'document')),
  target TEXT NOT NULL,
  instruction TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (document_id, revision)
);
CREATE TABLE document_memory (
  document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
  summary TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
