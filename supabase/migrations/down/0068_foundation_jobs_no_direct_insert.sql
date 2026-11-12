-- Undo 0068: the API role may again insert a job row itself (with case.upload).
set role app_migrator;

grant insert on app.jobs to app_api;
create policy jobs_add on app.jobs for insert to app_api
  with check (app.can_access_case(case_id, 'case.upload'));

reset role;
