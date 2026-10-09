create table sources (
  id uuid primary key,
  base_id uuid not null references knowledge_bases (id),
  name text not null check (char_length(name) between 1 and 120),
  state text not null check (state in ('active', 'retired')),
  revision integer not null default 0 check (revision >= 0),
  unique (base_id, name)
);
