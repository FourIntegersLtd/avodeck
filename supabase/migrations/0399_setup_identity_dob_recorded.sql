-- 0399 setup (slice C): lets every case member learn whether the subject's date of birth is recorded (never the
-- value), beside app.case_identity_recorded() from 0396. The screen asks for the date when only the name is there.

set role app_migrator;

create function app.case_identity_dob_recorded(p_case_id uuid) returns boolean
  language sql stable
  security definer
  set search_path = ''
as $$
  select app.can_access_case(p_case_id, 'case.view_metadata')
    and exists (
      select 1 from app.case_subject_identity where case_id = p_case_id and date_of_birth is not null
    )
$$;
grant execute on function app.case_identity_dob_recorded(uuid) to app_api;

reset role;
