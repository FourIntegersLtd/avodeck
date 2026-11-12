-- 0075 foundation: the purge exemption is a marker no app role can write (audit 16, PV-20).
--
-- The append-only and locked-row guards stood aside when the custom setting `app.purging_case_id` named the row's
-- case. Any session can set a custom setting, so a `security definer` function reached from an app_api or app_worker
-- session (it runs as the table owner) could remove a row the guards protect by naming the row's case first.
--
-- The purge exemption is now a row in `app.case_purge_marks`, written and removed by `app.purge_case_rows` inside the
-- purge's own transaction (9010) and read through `app.case_is_purging(case_id)`. The app roles hold no grant on the
-- table and no function of theirs writes it; the row carries the writing transaction's id, so it counts only inside
-- that transaction. The guards call the helper (9010, run-last range, so each guard's latest definition is the one
-- replaced).

set role app_migrator;

create table app.case_purge_marks (
  purged_case uuid primary key,
  xid bigint not null,
  created_at timestamptz not null default now()
);
-- The column is not called case_id on purpose: this is not case data, it is a one-transaction marker, and a
-- `case_id` column would make the case-table registry (and the purge order) expect it to be purged like one.
alter table app.case_purge_marks enable row level security;
revoke all on app.case_purge_marks from public, app_api, app_worker;

create function app.case_is_purging(p_case_id uuid) returns boolean
  language sql
  security definer
  set search_path = ''
as $$
  select exists (select 1 from app.case_purge_marks m where m.purged_case = p_case_id and m.xid = txid_current())
$$;
revoke execute on function app.case_is_purging(uuid) from public;
grant execute on function app.case_is_purging(uuid) to app_api, app_worker;

reset role;
