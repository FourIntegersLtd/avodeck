-- Down for 0002 foundation state machines.
drop trigger if exists pages_guard on app.pages;
drop trigger if exists memberships_guard on app.memberships;
drop trigger if exists jobs_guard on app.jobs;
drop trigger if exists cases_guard on app.cases;
drop function if exists app.pages_guard(), app.memberships_guard(), app.jobs_guard(), app.cases_guard(),
  app.case_status_allowed(text, text), app.refuse(text);
