create table facts (
  id uuid primary key,
  project_id uuid not null references projects (id),
  fact_key text not null check (char_length(fact_key) between 1 and 100),
  label text not null check (char_length(label) between 1 and 120),
  revision integer not null default 0 check (revision >= 0),
  updated_at timestamptz not null default now(),
  unique (project_id, fact_key)
);
