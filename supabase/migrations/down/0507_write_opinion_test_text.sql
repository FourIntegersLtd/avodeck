-- Down for 0507 write.
set role app_migrator;

create or replace function app.opinions_history() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  insert into app.opinion_versions
    (case_id, opinion_id, version_no, body, citation_refs, view, provisional, restored_from, saved_by)
  values
    (new.case_id, new.id, new.version, new.body, new.citation_refs, new.view, new.provisional, new.restored_from,
     new.updated_by);
  return null;
end
$$;
revoke all on function app.opinions_history() from public;

alter table app.opinion_versions drop column if exists test_text;
alter table app.opinions drop column if exists test_text;

reset role;
