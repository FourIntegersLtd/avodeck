-- Slice D fixes (docs/slices/D-understand.md):
-- * consultation_notes: a note is its author's own. Only the author reads or changes it; each person keeps
--   at most one note per consultation, so a new named expert writes their own beside the old one.
-- * fact_question_links: removing a question keeps its links, marked `stale` with no question.
-- * findings: one row per check and evidence set, so a re-run can never repeat a finding.
-- * case_guideline_versions: the API records each version the case was shown, with a fingerprint of the
--   pack's entry, so a later change to that entry can be noticed.
set role app_migrator;

-- consultation notes -----------------------------------------------------------------------------------------
alter table app.consultation_notes drop constraint consultation_notes_case_id_fact_id_key;
alter table app.consultation_notes add constraint consultation_notes_one_each unique (case_id, fact_id, person_id);

drop policy consultation_notes_view on app.consultation_notes;
drop policy consultation_notes_change on app.consultation_notes;
create policy consultation_notes_view on app.consultation_notes for select to app_api
  using (person_id = app.current_person_id() and app.can_access_case(case_id, 'case.view_content'));
-- The author keeps their own note even after the named expert changes; adding one still needs
-- `consultation.note` (consultation_notes_add).
create policy consultation_notes_change on app.consultation_notes for update to app_api
  using (person_id = app.current_person_id() and app.can_access_case(case_id, 'case.view_content'))
  with check (person_id = app.current_person_id() and app.can_access_case(case_id, 'case.view_content'));

-- links outlive their question -------------------------------------------------------------------------------
alter table app.fact_question_links alter column question_id drop not null;
alter table app.fact_question_links drop constraint fact_question_links_question_id_fkey;
alter table app.fact_question_links add constraint fact_question_links_question_id_fkey
  foreign key (question_id) references app.questions (id) on delete set null;

create or replace function app.fact_question_links_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.question_id is null then
      perform app.refuse('a link starts with a question');
    end if;
    if (new.source, new.status) not in (('ai', 'suggested'), ('expert', 'confirmed')) then
      perform app.refuse('a link starts suggested, or confirmed when the expert adds it');
    end if;
    new.version := 1;
    return new;
  end if;
  if new.question_id is null and old.question_id is not null then
    -- The question was removed: the link stays, as stale.
    new.status := 'stale';
  elsif new.question_id is distinct from old.question_id then
    perform app.refuse('a link keeps its fact, question and source');
  end if;
  if (new.case_id, new.fact_id, new.source) is distinct from (old.case_id, old.fact_id, old.source) then
    perform app.refuse('a link keeps its fact, question and source');
  end if;
  if old.status = 'stale' and old.question_id is null and new.status <> 'stale' then
    perform app.refuse('a link whose question was removed stays stale');
  end if;
  if new.status = 'suggested' and old.status <> 'suggested' then
    perform app.refuse('a decided link never goes back to suggested');
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;

-- one finding per check and evidence -------------------------------------------------------------------------
create function app.sorted_uuids(ids uuid[]) returns uuid[]
  language sql
  immutable
  set search_path = ''
as $$ select coalesce(array_agg(x order by x), '{}') from unnest(ids) as x $$;

create unique index findings_one_each on app.findings (case_id, check_key, app.sorted_uuids(evidence_fact_ids));

-- the guideline versions each case was shown -----------------------------------------------------------------
alter table app.case_guideline_versions add column entry_hash text check (length(entry_hash) <= 64);
alter table app.case_guideline_versions drop constraint case_guideline_versions_pkey;
alter table app.case_guideline_versions add primary key (case_id, code, version_label);
grant select, insert on app.case_guideline_versions to app_api;
create policy case_guideline_versions_add on app.case_guideline_versions for insert to app_api
  with check (app.can_access_case(case_id, 'case.view_content'));

reset role;
