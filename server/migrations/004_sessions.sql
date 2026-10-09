create table sessions (
  id uuid primary key,
  user_id uuid not null references users (id),
  token_hash text not null unique,
  csrf_token text not null,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default now()
);
