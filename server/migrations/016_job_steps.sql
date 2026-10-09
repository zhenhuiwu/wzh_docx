create table job_steps (
  job_id uuid not null references jobs (id),
  step_index integer not null check (step_index >= 0),
  name text not null,
  status text not null,
  fence bigint not null,
  checkpoint text not null default '',
  primary key (job_id, step_index, fence)
);
