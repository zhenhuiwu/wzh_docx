create table files (
  id uuid primary key,
  name text not null check (char_length(name) between 1 and 255),
  media_type text not null check (char_length(media_type) between 1 and 120),
  size_bytes bigint not null check (size_bytes >= 0),
  sha256 text not null check (sha256 ~ '^[a-f0-9]{64}$'),
  status text not null check (status in ('stored', 'quarantined', 'unavailable')),
  purpose text not null check (
    purpose in ('source', 'template', 'evidence', 'attachment')
  ),
  project_id uuid,
  base_id uuid,
  storage_key text not null unique check (storage_key !~ '[\\/]'),
  created_at timestamptz not null default now()
);
