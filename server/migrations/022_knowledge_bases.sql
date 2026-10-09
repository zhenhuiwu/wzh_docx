create table knowledge_bases (
  id uuid primary key,
  name text not null check (char_length(name) between 1 and 120),
  summary text not null default '' check (char_length(summary) <= 2000),
  scope text not null check (scope in ('organization', 'project')),
  project_id uuid references projects (id),
  owner_id uuid not null references users (id),
  state text not null check (state in ('active', 'archived')),
  revision integer not null default 0 check (revision >= 0),
  updated_at timestamptz not null default now()
);
