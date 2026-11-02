-- 0049 foundation: `ai_request_slots`, the Bedrock on-demand throughput scheduler.
--
-- Claude Sonnet via Bedrock on-demand is throttled account-wide at a fixed requests-per-minute
-- ceiling (`AiSettings.bedrock_rpm_limit`), not raisable per tenant or per case. `app.core.ai.kit`
-- coordinates every worker job's calls against that shared ceiling before it calls Bedrock:
-- * one row per attempt to get a slot (`requested_at`); `granted_at` is set once the call may proceed.
-- * `app.jobs.last_granted_at` is the fairness clock: among jobs currently waiting, the one whose own
--   last grant is oldest (or who has never been granted one) goes next, so one job cannot starve
--   another by polling faster.
-- Worker-only (like `job_steps`, migration 0007): the API never schedules Bedrock slots directly.

alter table app.jobs add column last_granted_at timestamptz;

create table app.ai_request_slots (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references app.jobs (id) on delete cascade,
  case_id uuid not null references app.cases (id) on delete cascade,
  created_at timestamptz not null default now(),
  requested_at timestamptz not null default now(),
  granted_at timestamptz
);
create index ai_request_slots_granted on app.ai_request_slots (granted_at);
create index ai_request_slots_pending on app.ai_request_slots (job_id) where granted_at is null;

alter table app.ai_request_slots enable row level security;
revoke all on app.ai_request_slots from app_api;

create policy ai_request_slots_job on app.ai_request_slots to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

-- Purged immediately before `jobs` (app.case_purge_order(), migration 0017), like `job_steps`: its
-- `job_id` references `jobs` on delete cascade, so it must be removed first.
set role app_migrator;

do $do$
declare
  current_order text[] := app.case_purge_order();
  mine text := 'ai_request_slots';
  at integer;
  spliced text[];
begin
  at := array_position(current_order, 'jobs');
  spliced := current_order[1:at - 1] || array[mine] || current_order[at:];
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    spliced
  );
end
$do$;

reset role;
