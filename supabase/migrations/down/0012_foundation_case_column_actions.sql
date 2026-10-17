-- Down for 0012 foundation: case column actions.
drop trigger if exists cases_actor on app.cases;
drop function if exists app.cases_actor();
drop function if exists app.case_ever_served(uuid);
