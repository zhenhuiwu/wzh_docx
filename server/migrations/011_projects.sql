create table projects (
  id uuid primary key,
  name text not null check (char_length(name) between 1 and 120),
  description text not null default '' check (char_length(description) <= 2000),
  organization text not null default '' check (char_length(organization) <= 120),
  department text not null default '' check (char_length(department) <= 120),
  group_name text not null default '' check (char_length(group_name) <= 120),
  owner_id uuid not null references users (id),
  period_start date,
  period_end date,
  status text not null check (status in ('not_started', 'active', 'paused', 'completed')),
  archived_at timestamptz,
  revision integer not null default 0 check (revision >= 0),
  software_version text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
