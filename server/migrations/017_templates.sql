create table templates (
  id uuid primary key,
  name text not null check (char_length(name) between 1 and 120),
  scenario text not null check (char_length(scenario) between 1 and 120),
  status text not null check (
    status in ('unadapted', 'draft', 'validating', 'ready', 'published', 'retired')
  ),
  source_file_id uuid not null,
  current_version_id uuid,
  revision integer not null default 0 check (revision >= 0),
  updated_at timestamptz not null default now()
);
