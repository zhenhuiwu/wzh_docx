CREATE TABLE writing_skills (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
  name_key TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT '' CHECK(length(description) <= 500),
  instructions TEXT NOT NULL CHECK(length(instructions) BETWEEN 1 AND 12000),
  base_kind TEXT NOT NULL CHECK(base_kind IN ('general', 'srs', 'sdd')),
  forbidden_terms TEXT NOT NULL DEFAULT '[]' CHECK(
    json_valid(forbidden_terms) AND json_type(forbidden_terms) = 'array'
    AND json_array_length(forbidden_terms) <= 30
  ),
  max_chars INTEGER CHECK(max_chars IS NULL OR max_chars BETWEEN 100 AND 20000),
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  builtin INTEGER NOT NULL DEFAULT 0 CHECK(builtin IN (0, 1)),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX writing_skills_status_updated ON writing_skills(status, updated_at DESC);

CREATE TABLE writing_skill_requests (
  user_id TEXT NOT NULL REFERENCES users(id),
  idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
  request_hash TEXT NOT NULL,
  skill_id TEXT NOT NULL REFERENCES writing_skills(id),
  PRIMARY KEY(user_id, idempotency_key)
);
