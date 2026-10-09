-- AI test plans and immutable execution history, independent of authored documents.
CREATE TABLE test_tasks (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  name TEXT NOT NULL,
  plan_file_id TEXT NOT NULL REFERENCES files(id),
  report_file_id TEXT REFERENCES files(id),
  config_json TEXT NOT NULL,
  cases_json TEXT NOT NULL DEFAULT '[]',
  revision INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'new',
  error TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX test_tasks_project_updated ON test_tasks(project_id, updated_at DESC);
CREATE TABLE test_task_runs (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES test_tasks(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'running',
  snapshot_json TEXT NOT NULL,
  result_json TEXT NOT NULL DEFAULT '{}',
  stop_requested INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ended_at TEXT
);
CREATE INDEX test_task_runs_task_started ON test_task_runs(task_id, started_at DESC);
CREATE UNIQUE INDEX test_task_runs_one_active ON test_task_runs(task_id) WHERE status = 'running';
