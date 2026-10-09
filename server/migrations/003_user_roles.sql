create table user_roles (
  user_id uuid not null references users (id),
  role text not null check (
    role in (
      'system_admin',
      'project_creator',
      'template_manager',
      'knowledge_manager',
      'auditor'
    )
  ),
  primary key (user_id, role)
);
