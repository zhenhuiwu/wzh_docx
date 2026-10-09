create table fact_sources (
  revision_id uuid not null references fact_revisions (id),
  source_version_id uuid not null,
  primary key (revision_id, source_version_id)
);
