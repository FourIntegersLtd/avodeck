-- Down for 0005 foundation auth.
drop function if exists app.audit_anonymous(text, text), app.confirm_email_change(text),
  app.session_resolve(text, interval, interval, boolean);
drop table if exists app.email_changes, app.mfa_recovery_codes, app.sessions;
