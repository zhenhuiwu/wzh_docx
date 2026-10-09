create table document_blocks (
  id uuid primary key,
  document_id uuid not null references documents (id),
  block_key text not null,
  title text not null,
  kind text not null,
  revision integer not null default 0 check (revision >= 0),
  current_revision_id uuid,
  approved_revision_id uuid,
  validity text not null check (validity in ('current', 'needs_review', 'conflict')),
  unique (document_id, block_key)
);
