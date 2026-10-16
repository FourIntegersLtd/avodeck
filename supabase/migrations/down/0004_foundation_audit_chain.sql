-- Down for 0004 foundation audit chain.
drop trigger if exists audit_log_chain on app.audit_log;
drop function if exists app.audit_log_chain(),
  app.audit_hash(bigint, uuid, uuid, uuid, text, text, timestamptz, text);
