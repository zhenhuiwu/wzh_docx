create table template_versions (
  id uuid primary key,
  template_id uuid not null references templates (id),
  version text not null check (char_length(version) between 1 and 64),
  skill_version text not null check (char_length(skill_version) between 1 and 64),
  status text not null check (
    status in ('draft', 'validating', 'ready', 'published', 'retired')
  ),
  revision integer not null default 0 check (revision >= 0),
  source_file_id uuid not null,
  template_file_id uuid not null,
  skill_file_id uuid not null,
  rules_file_id uuid not null,
  blocks jsonb not null default '[]'::jsonb,
  validation_job_id uuid,
  validation_passed boolean not null default false,
  format_approved_by uuid,
  format_approved_at timestamptz,
  format_approver_name text,
  renderer_environment_id uuid,
  sample_present boolean not null default false,
  updated_at timestamptz not null default now()
);
