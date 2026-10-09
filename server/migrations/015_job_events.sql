create table job_events (
  job_id uuid not null references jobs (id),
  sequence integer not null check (sequence >= 1),
  event_type text not null,
  status text not null,
  stage_name text not null default '',
  text_body text not null default '',
  created_at timestamptz not null default now(),
  primary key (job_id, sequence)
);
