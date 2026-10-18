-- 0507 write (slice E): the legal test in the expert's own words (screen 15; data dictionary opinions.test_text).
-- Sealed under the case key like the body; every saved version keeps it, so a restore brings it back.

set role app_migrator;

alter table app.opinions add column test_text bytea;
alter table app.opinion_versions add column test_text bytea;

create or replace function app.opinions_history() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  insert into app.opinion_versions
    (case_id, opinion_id, version_no, body, citation_refs, view, provisional, restored_from, saved_by, test_text)
  values
    (new.case_id, new.id, new.version, new.body, new.citation_refs, new.view, new.provisional, new.restored_from,
     new.updated_by, new.test_text);
  return null;
end
$$;
revoke all on function app.opinions_history() from public;

reset role;
