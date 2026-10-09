create table csrf_challenges (
  id uuid primary key,
  token_hash text not null,
  expires_at timestamptz not null,
  used_at timestamptz
);
