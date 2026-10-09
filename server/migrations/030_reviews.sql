create table reviews (
  id uuid primary key,
  project_id uuid not null references projects (id),
  document_id uuid not null references documents (id),
  block_revision_id uuid not null references block_revisions (id),
  submitter_id uuid not null references users (id),
  reviewer_id uuid not null references users (id),
  state text not null check (state in ('pending', 'approved', 'rejected')),
  reason text not null default '' check (char_length(reason) <= 1000),
  revision integer not null default 0 check (revision >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
