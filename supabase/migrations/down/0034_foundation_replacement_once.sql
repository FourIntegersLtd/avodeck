-- Undo 0034: app.create_replacement_case as in 0033.
set role app_migrator;

create or replace function app.create_replacement_case(p_case uuid, p_named_expert uuid, p_label text, p_consented boolean)
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

reset role;
