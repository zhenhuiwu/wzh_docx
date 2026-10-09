create table source_versions (
  id uuid primary key,
  source_id uuid not null references sources (id),
  version_number integer not null check (version_number >= 1),
  file_id uuid not null,
  media_type text not null,
  status text not null,
  message text,
  revision integer not null default 0,
  updated_at timestamptz not null default now(),
  unique (source_id, version_number),
  unique (source_id, file_id)
);
