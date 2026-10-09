create table block_revisions (
  id uuid primary key,
  block_id uuid not null references document_blocks (id),
  base_revision integer not null check (base_revision >= 0),
  origin text not null check (origin in ('human', 'model', 'merged')),
  disposition text not null check (disposition in ('candidate', 'current', 'superseded', 'discarded')),
  review_state text not null check (review_state in ('draft', 'pending_review', 'approved', 'rejected')),
  content jsonb not null,
  created_at timestamptz not null default now(),
  created_by uuid not null references users (id)
);
