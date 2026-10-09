-- Complete the first SQLite schema without discarding existing rows.
ALTER TABLE files DROP COLUMN content_type;
ALTER TABLE files ADD COLUMN name TEXT NOT NULL DEFAULT '';
ALTER TABLE files ADD COLUMN media_type TEXT NOT NULL DEFAULT '';
ALTER TABLE files ADD COLUMN sha256 TEXT NOT NULL DEFAULT '';
ALTER TABLE files ADD COLUMN status TEXT NOT NULL DEFAULT 'stored';
ALTER TABLE files ADD COLUMN purpose TEXT NOT NULL DEFAULT 'attachment';
ALTER TABLE files ADD COLUMN project_id TEXT;
ALTER TABLE files ADD COLUMN base_id TEXT;

ALTER TABLE users DROP COLUMN password_salt;
ALTER TABLE csrf_challenges ADD COLUMN used_at TEXT;

CREATE TABLE login_guards (
  account_key TEXT PRIMARY KEY,
  failed_count INTEGER NOT NULL DEFAULT 0,
  window_started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE idempotency_keys (
  user_id TEXT NOT NULL REFERENCES users(id),
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  file_id TEXT,
  result_user_id TEXT,
  result_project_id TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, idempotency_key)
);
CREATE TABLE user_admin_events (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  actor_id TEXT NOT NULL REFERENCES users(id),
  action TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE projects ADD COLUMN organization TEXT NOT NULL DEFAULT '';
ALTER TABLE projects ADD COLUMN department TEXT NOT NULL DEFAULT '';
ALTER TABLE projects ADD COLUMN group_name TEXT NOT NULL DEFAULT '';
ALTER TABLE projects ADD COLUMN period_start TEXT;
ALTER TABLE projects ADD COLUMN period_end TEXT;
ALTER TABLE projects ADD COLUMN archived_at TEXT;
ALTER TABLE projects ADD COLUMN software_version TEXT;

CREATE TABLE project_members (
  project_id TEXT NOT NULL REFERENCES projects(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  role TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (project_id, user_id)
);
CREATE TABLE jobs (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id),
  created_by TEXT NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0,
  target_kind TEXT NOT NULL,
  target_id TEXT,
  target_name TEXT NOT NULL,
  stage_name TEXT NOT NULL DEFAULT '',
  completed_steps INTEGER NOT NULL DEFAULT 0,
  total_steps INTEGER NOT NULL DEFAULT 1,
  last_sequence INTEGER NOT NULL DEFAULT 0,
  blocking_reason TEXT,
  result_revision_id TEXT,
  started_at TEXT,
  ended_at TEXT,
  fence INTEGER NOT NULL DEFAULT 0,
  lease_owner TEXT,
  lease_until TEXT,
  checkpoint TEXT NOT NULL DEFAULT '',
  attempt INTEGER NOT NULL DEFAULT 0,
  cancel_intent INTEGER NOT NULL DEFAULT 0,
  step_plan TEXT NOT NULL DEFAULT 'pure:work',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE job_events (
  job_id TEXT NOT NULL REFERENCES jobs(id),
  sequence INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  status TEXT NOT NULL,
  stage_name TEXT NOT NULL DEFAULT '',
  text_body TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (job_id, sequence)
);
CREATE TABLE job_steps (
  job_id TEXT NOT NULL REFERENCES jobs(id),
  step_index INTEGER NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL,
  fence INTEGER NOT NULL,
  checkpoint TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (job_id, step_index, fence)
);

ALTER TABLE documents ADD COLUMN purpose TEXT NOT NULL DEFAULT '';
ALTER TABLE documents ADD COLUMN validity TEXT NOT NULL DEFAULT 'current';
CREATE TABLE document_blocks (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id),
  block_key TEXT NOT NULL,
  title TEXT NOT NULL,
  kind TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0,
  current_revision_id TEXT,
  approved_revision_id TEXT,
  validity TEXT NOT NULL DEFAULT 'current',
  UNIQUE (document_id, block_key)
);
CREATE TABLE block_revisions (
  id TEXT PRIMARY KEY,
  block_id TEXT NOT NULL REFERENCES document_blocks(id),
  base_revision INTEGER NOT NULL,
  origin TEXT NOT NULL,
  disposition TEXT NOT NULL,
  review_state TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by TEXT NOT NULL REFERENCES users(id)
);
CREATE TABLE reviews (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  document_id TEXT NOT NULL REFERENCES documents(id),
  block_revision_id TEXT NOT NULL REFERENCES block_revisions(id),
  submitter_id TEXT NOT NULL REFERENCES users(id),
  reviewer_id TEXT NOT NULL REFERENCES users(id),
  state TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE document_plans (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  name TEXT NOT NULL,
  purpose TEXT NOT NULL,
  template_id TEXT REFERENCES templates(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (project_id, name, purpose)
);
CREATE INDEX document_plans_project_order ON document_plans(project_id, created_at, id);

CREATE TABLE knowledge_bases (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  scope TEXT NOT NULL,
  project_id TEXT REFERENCES projects(id),
  owner_id TEXT NOT NULL REFERENCES users(id),
  state TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE knowledge_access (
  base_id TEXT NOT NULL REFERENCES knowledge_bases(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  role TEXT NOT NULL,
  PRIMARY KEY (base_id, user_id)
);
CREATE TABLE sources (
  id TEXT PRIMARY KEY,
  base_id TEXT NOT NULL REFERENCES knowledge_bases(id),
  name TEXT NOT NULL,
  state TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0,
  UNIQUE (base_id, name)
);
CREATE TABLE source_versions (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(id),
  version_number INTEGER NOT NULL,
  file_id TEXT NOT NULL REFERENCES files(id),
  media_type TEXT NOT NULL,
  status TEXT NOT NULL,
  message TEXT,
  revision INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (source_id, version_number),
  UNIQUE (source_id, file_id)
);
CREATE TABLE facts (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  fact_key TEXT NOT NULL,
  label TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (project_id, fact_key)
);
CREATE TABLE fact_revisions (
  id TEXT PRIMARY KEY,
  fact_id TEXT NOT NULL REFERENCES facts(id),
  revision INTEGER NOT NULL,
  value_text TEXT,
  state TEXT NOT NULL,
  decision_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (fact_id, revision)
);
CREATE TABLE fact_sources (
  revision_id TEXT NOT NULL REFERENCES fact_revisions(id),
  source_version_id TEXT NOT NULL REFERENCES source_versions(id),
  PRIMARY KEY (revision_id, source_version_id)
);
CREATE TABLE project_knowledge_bindings (
  project_id TEXT NOT NULL REFERENCES projects(id),
  base_id TEXT NOT NULL REFERENCES knowledge_bases(id),
  PRIMARY KEY (project_id, base_id)
);
