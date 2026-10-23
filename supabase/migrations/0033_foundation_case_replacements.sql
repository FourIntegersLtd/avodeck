-- 0033 foundation: a replacement case for a served one (scenario S3 after serving, architecture 10.1
-- "Reassignment"). Called through `core/contracts/replacement.py` by slice O's
-- `POST /v1/cases/{case_id}/replacement` (`case.reassign_named_expert` 🔒).
--
-- case_replacements                 one row per replacement case: the served case it replaces and whether
--                                   the instructing party consented to the bundle being copied (slice A
--                                   copies only when it is true; copying is not built yet). Written only by
--                                   app.create_replacement_case(); never changed. Kept after either case is
--                                   deleted, as the record of consent (ids, a yes/no and a date). Not in the
--                                   purge order (a table in the foundation range cannot join it on a fresh
--                                   database).
-- app.create_replacement_case(case, expert, label, consented)
--                                   for someone with `case.reassign_named_expert` on the case (anyone else:
--                                   42501): refuses a case never served (AV409 `not_served`: reassign it
--                                   instead) and a new expert who is the case's own or not a current owner,
--                                   admin or expert member of its organisation (22023). Otherwise creates a
--                                   draft case in the same organisation, packs (and their versions) and
--                                   label (or `label`), named to the new expert, links it `replaces` to the
--                                   served case (slice C's `case_links`) and records the consent. The person
--                                   creating it does not join it: `cases_creator_joins` (0019, redefined
--                                   here) skips a replacement. Nothing from the bundle is copied.

set role app_migrator;

create table app.case_replacements (
  case_id uuid primary key references app.cases (id) on delete cascade,
  replaces_case_id uuid not null references app.cases (id) on delete cascade,
  instructing_party_consented boolean not null,
  recorded_by uuid not null references app.people (id),
  recorded_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  constraint case_replacements_another_case check (case_id <> replaces_case_id)
);
create index case_replacements_replaces on app.case_replacements (replaces_case_id);

alter table app.case_replacements enable row level security;
revoke all on app.case_replacements from app_api, app_worker;
grant select on app.case_replacements to app_api, app_worker;
create policy case_replacements_view on app.case_replacements for select to app_api
  using (app.can_access_case(case_id, 'case.view_metadata')
         or app.can_access_case(replaces_case_id, 'case.view_metadata'));
create policy case_replacements_job on app.case_replacements for select to app_worker
  using (case_id = app.current_job_case_id() or replaces_case_id = app.current_job_case_id());

create or replace function app.cases_creator_joins() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  me constant uuid := app.current_person_id();
begin
  if me is not null and me is distinct from new.named_expert_id
     and coalesce(current_setting('app.case_replacement', true), '') <> new.id::text then
    insert into app.case_members (case_id, person_id, case_role, added_by) values (new.id, me, 'assistant', me);
  end if;
  return new;
end
$$;

create function app.create_replacement_case(p_case uuid, p_named_expert uuid, p_label text, p_consented boolean)
  returns uuid
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  me constant uuid := app.current_person_id();
  served app.cases;
  made uuid := gen_random_uuid();
begin
  if me is null or not app.can_access_case(p_case, 'case.reassign_named_expert') then
    raise exception using errcode = '42501', message = 'a replacement needs case.reassign_named_expert';
  end if;
  select * into served from app.cases c where c.id = p_case for share;
  if not app.case_ever_served(p_case) then
    perform app.refuse('not_served');
  end if;
  if p_named_expert is null or p_named_expert = served.named_expert_id
     or not app.is_expert_member(served.organisation_id, p_named_expert) then
    raise exception using errcode = '22023', message = 'named_expert_id';
  end if;
  if p_consented is null then
    raise exception using errcode = '22023', message = 'instructing_party_consented';
  end if;
  perform set_config('app.case_replacement', made::text, true);
  insert into app.cases (id, organisation_id, named_expert_id, jurisdiction, case_type, specialty, expert_role,
                         label, party_role, instructing_party_type, court_track, subject_status, period_start,
                         period_end, pack_versions)
  values (made, served.organisation_id, p_named_expert, served.jurisdiction, served.case_type, served.specialty,
          served.expert_role, coalesce(nullif(btrim(p_label), ''), served.label), served.party_role,
          served.instructing_party_type, served.court_track, served.subject_status, served.period_start,
          served.period_end, served.pack_versions);
  perform set_config('app.case_replacement', '', true);
  insert into app.case_links (case_id, linked_case_id, kind, created_by) values (made, p_case, 'replaces', me);
  insert into app.case_replacements (case_id, replaces_case_id, instructing_party_consented, recorded_by)
  values (made, p_case, p_consented, me);
  return made;
end
$$;

revoke execute on function app.create_replacement_case(uuid, uuid, text, boolean) from public;
grant execute on function app.create_replacement_case(uuid, uuid, text, boolean) to app_api;

reset role;
