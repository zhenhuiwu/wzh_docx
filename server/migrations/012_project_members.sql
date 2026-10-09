create table project_members (
  project_id uuid not null references projects (id),
  user_id uuid not null references users (id),
  role text not null check (role in ('owner', 'editor', 'reviewer', 'viewer')),
  revision integer not null default 0 check (revision >= 0),
  primary key (project_id, user_id)
);
