-- 0013 foundation: a kept Idempotency-Key answer names its case, and is sealed at rest (core/http.py).
-- Answers kept before this migration were stored unsealed; they are dropped (they last 24 hours anyway).

set role app_migrator;

delete from app.idempotency_keys;
alter table app.idempotency_keys add column case_id uuid references app.cases (id) on delete cascade;
create index idempotency_keys_case on app.idempotency_keys (case_id) where case_id is not null;

reset role;
