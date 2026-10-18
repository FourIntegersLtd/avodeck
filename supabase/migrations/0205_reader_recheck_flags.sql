-- 0205 reader: "Please check" flags from the pipeline. When a page is read again (turned, or typed
-- out by the expert) and a checked fact's words are gone, slice A's job flags the fact
-- `flagged_for = 'recheck'` (core/contracts/fact_flags.py `flag_for_recheck`) with no person; the
-- flag goes when the expert checks the fact again.

set role app_migrator;

alter table app.fact_flags alter column person_id drop not null;
alter table app.fact_flags add constraint fact_flags_person check (person_id is not null or flagged_for = 'recheck');

grant insert on app.fact_flags to app_worker;

create policy fact_flags_recheck on app.fact_flags for insert to app_worker
  with check (case_id = app.current_job_case_id() and person_id is null and flagged_for = 'recheck'
    and exists (select 1 from app.facts f where f.id = fact_id and f.case_id = fact_flags.case_id));

reset role;
