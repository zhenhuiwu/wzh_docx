create table fact_revisions (
  id uuid primary key,
  fact_id uuid not null references facts (id),
  revision integer not null check (revision >= 0),
  value_text text,
  state text not null check (state in ('candidate', 'confirmed', 'superseded')),
  decision_reason text not null default '',
  created_at timestamptz not null default now(),
  unique (fact_id, revision)
);
