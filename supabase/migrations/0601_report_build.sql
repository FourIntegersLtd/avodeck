-- 0601 report: what slice F needs to build the report (architecture 4.7).
-- * `report_section_texts`: the named expert's own words for each report section that is not an opinion
--   (opinions are slice E's, declarations come from the pack, the generated appendices from Avodeck),
--   sealed under the case key, with citations in the opinion shape. Only `opinion.write` (the named
--   expert) writes them; people on the case read them; never deleted (the case purge removes them).
-- * `report_builds`: one row per built draft: the citation style and template it used, whether the
--   template fell back to the standard layout (F16), the page count and the pack versions. Only the
--   case's own `build_report` job writes it; it never changes.
-- * `report_settings.addressed_contents`: the jurisdiction pack's required contents the expert has
--   ticked as addressed in their report (screen 16); the final check reads them. The settings row also
--   records when it was made (timestamps guard).

set role app_migrator;

create table app.report_section_texts (
  case_id uuid not null references app.cases (id) on delete cascade,
  section text not null check (section ~ '^[a-z][a-z0-9_]{0,39}$'),
  body bytea not null,
  citation_refs jsonb not null default '[]' check (jsonb_typeof(citation_refs) = 'array'),
  updated_by uuid default app.current_person_id(),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (case_id, section)
);

create table app.report_builds (
  report_version_id uuid primary key references app.report_versions (id) on delete cascade,
  case_id uuid not null references app.cases (id) on delete cascade,
  job_id uuid,
  citation_style text not null check (citation_style in ('page', 'paragraph')),
  template_id uuid,
  template_fallback boolean not null default false,
  page_count integer not null check (page_count >= 0),
  pack_versions jsonb not null default '{}',
  created_at timestamptz not null default now()
);
create index report_builds_case on app.report_builds (case_id, created_at desc);

alter table app.report_settings add column created_at timestamptz not null default now();
alter table app.report_settings add column addressed_contents text[] not null default '{}'
  check (cardinality(addressed_contents) <= 100);

alter table app.report_section_texts enable row level security;
alter table app.report_builds enable row level security;

create policy report_section_texts_view on app.report_section_texts for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy report_section_texts_add on app.report_section_texts for insert to app_api
  with check (app.can_access_case(case_id, 'opinion.write'));
create policy report_section_texts_change on app.report_section_texts for update to app_api
  using (app.can_access_case(case_id, 'opinion.write')) with check (app.can_access_case(case_id, 'opinion.write'));
create policy report_section_texts_job on app.report_section_texts for select to app_worker
  using (case_id = app.current_job_case_id());

create policy report_builds_view on app.report_builds for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy report_builds_job on app.report_builds to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

revoke delete on app.report_section_texts from app_api, app_worker;
revoke insert, update, delete on app.report_builds from app_api;
revoke update, delete on app.report_builds from app_worker;

reset role;
