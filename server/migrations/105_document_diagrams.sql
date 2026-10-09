CREATE TABLE document_diagrams (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('flowchart', 'architecture')),
  source_revision INTEGER NOT NULL,
  graph_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX document_diagrams_document_created ON document_diagrams(document_id, created_at DESC);
