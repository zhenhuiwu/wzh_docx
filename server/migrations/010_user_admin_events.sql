create table user_admin_events (
  id uuid primary key,
  user_id uuid not null references users (id),
  actor_id uuid not null references users (id),
  action text not null check (char_length(action) between 1 and 80),
  reason text not null check (char_length(reason) between 1 and 1000),
  created_at timestamptz not null default now()
);
