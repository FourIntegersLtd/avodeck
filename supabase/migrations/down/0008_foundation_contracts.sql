-- Down for 0008 foundation contract tables and the upload sweep.
drop function if exists app.expire_uploads(date);
drop table if exists app.report_versions, app.questions, app.facts;
drop function if exists app.report_versions_guard(), app.report_locked(text), app.questions_guard(),
  app.facts_guard();
