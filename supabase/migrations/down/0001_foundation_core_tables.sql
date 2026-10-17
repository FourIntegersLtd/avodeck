-- Down for 0001 foundation core tables.
drop table if exists
  app.idempotency_keys, app.audit_log, app.feature_flags, app.notifications, app.jobs, app.pages,
  app.documents, app.case_members, app.cases, app.notification_preferences, app.memberships,
  app.organisations, app.experts, app.people
  cascade;
