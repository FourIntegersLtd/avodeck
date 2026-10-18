-- Down for 0401 understand fixes.
set role app_migrator;
drop policy if exists case_guideline_versions_add on app.case_guideline_versions;
alter table app.case_guideline_versions drop constraint case_guideline_versions_pkey;
delete from app.case_guideline_versions a using app.case_guideline_versions b
  where a.case_id = b.case_id and a.code = b.code and a.created_at < b.created_at;
alter table app.case_guideline_versions add primary key (case_id, code);
alter table app.case_guideline_versions drop column if exists entry_hash;

drop index if exists app.findings_one_each;
drop function if exists app.sorted_uuids(uuid[]);

delete from app.fact_question_links where question_id is null;
alter table app.fact_question_links drop constraint fact_question_links_question_id_fkey;
alter table app.fact_question_links add constraint fact_question_links_question_id_fkey
  foreign key (question_id) references app.questions (id) on delete cascade;
alter table app.fact_question_links alter column question_id set not null;
create or replace function app.fact_question_links_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if (new.source, new.status) not in (('ai', 'suggested'), ('expert', 'confirmed')) then
      perform app.refuse('a link starts suggested, or confirmed when the expert adds it');
    end if;
    new.version := 1;
    return new;
  end if;
  if (new.case_id, new.fact_id, new.question_id, new.source)
     is distinct from (old.case_id, old.fact_id, old.question_id, old.source) then
    perform app.refuse('a link keeps its fact, question and source');
  end if;
  if new.status = 'suggested' and old.status <> 'suggested' then
    perform app.refuse('a decided link never goes back to suggested');
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;

drop policy consultation_notes_view on app.consultation_notes;
drop policy consultation_notes_change on app.consultation_notes;
create policy consultation_notes_view on app.consultation_notes for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy consultation_notes_change on app.consultation_notes for update to app_api
  using (app.can_access_case(case_id, 'consultation.note') and person_id = app.current_person_id())
  with check (app.can_access_case(case_id, 'consultation.note') and person_id = app.current_person_id());
alter table app.consultation_notes drop constraint consultation_notes_one_each;
delete from app.consultation_notes a using app.consultation_notes b
  where a.case_id = b.case_id and a.fact_id = b.fact_id and a.updated_at < b.updated_at;
alter table app.consultation_notes add constraint consultation_notes_case_id_fact_id_key unique (case_id, fact_id);
reset role;
