-- Down for 0013 foundation: idempotency keys lose their case.
drop index if exists app.idempotency_keys_case;
alter table app.idempotency_keys drop column if exists case_id;
