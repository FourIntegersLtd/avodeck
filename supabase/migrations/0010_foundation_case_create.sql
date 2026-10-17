-- 0010 foundation: creating a case (POST /v1/cases).
-- Rule 9: row-level security repeats the API's check. The restricted role may insert a case only
-- when it is named to the caller and the caller is an owner, admin or expert of its organisation.
-- (Naming another expert, which an assistant may do, comes with slice C/O.) The cases_guard
-- trigger already requires a new case to start as draft.
create policy cases_create on app.cases for insert to app_api
  with check (
    named_expert_id = app.current_person_id()
    and exists (
      select 1 from app.memberships m
      where m.organisation_id = cases.organisation_id
        and m.person_id = app.current_person_id()
        and m.ended_at is null
        and m.role in ('owner', 'admin', 'expert')
    )
  );
