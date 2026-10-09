create table users (
  id uuid primary key,
  account text not null check (char_length(account) between 1 and 64),
  account_key text not null unique check (char_length(account_key) between 1 and 64),
  name text not null check (char_length(name) between 1 and 120),
  department text not null default '' check (char_length(department) <= 120),
  password_hash text not null,
  status text not null check (status in ('active', 'disabled')),
  revision integer not null default 0 check (revision >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  last_login_at timestamptz
);
