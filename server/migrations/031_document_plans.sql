create table document_plans (
  id uuid primary key,
  project_id uuid not null references projects (id),
  name text not null check (char_length(name) between 1 and 120),
  purpose text not null check (char_length(purpose) between 1 and 200),
  template_id uuid references templates (id),
  created_at timestamptz not null default now(),
  unique (project_id, name, purpose)
);
create index document_plans_project_order on document_plans (project_id, created_at, id);
