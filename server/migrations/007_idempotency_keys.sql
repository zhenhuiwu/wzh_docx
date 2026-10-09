create table idempotency_keys (
  user_id uuid not null references users (id),
  idempotency_key text not null,
  request_hash text not null,
  file_id uuid,
  created_at timestamptz not null default now(),
  primary key (user_id, idempotency_key)
);
