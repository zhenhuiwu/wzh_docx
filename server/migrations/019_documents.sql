create table documents (
  id uuid primary key,
  project_id uuid not null references projects (id),
  name text not null check (char_length(name) between 1 and 120),
  purpose text not null check (char_length(purpose) between 1 and 200),
  template_version_id uuid not null references template_versions (id),
  owner_id uuid not null references users (id),
  state text not null check (state in ('preparing', 'draft', 'pending_review', 'ready', 'published')),
  validity text not null check (validity in ('current', 'needs_review', 'conflict')),
  revision integer not null default 0 check (revision >= 0),
  updated_at timestamptz not null default now(),
  unique (project_id, name, purpose)
);
