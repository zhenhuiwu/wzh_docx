create table knowledge_access (
  base_id uuid not null references knowledge_bases (id),
  user_id uuid not null references users (id),
  role text not null check (role in ('reader', 'maintainer')),
  primary key (base_id, user_id)
);
