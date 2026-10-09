create table project_knowledge_bindings (
  project_id uuid not null references projects (id),
  base_id uuid not null references knowledge_bases (id),
  primary key (project_id, base_id)
);
