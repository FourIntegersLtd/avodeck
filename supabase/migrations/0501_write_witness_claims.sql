-- 0501 write (slice E): what a witness statement says, set beside the records (T-14c, screen 14).
-- Each claim is an exact run of the statement's words (`text_verbatim`, encrypted, with its `word_ids`),
-- the AI's label (`verdict`: supports / contradicts / not_addressed) and the verified facts it was
-- compared with, at the versions compared. Written only by the worker; the named expert decides
-- (state-machines.md, Witness claim): suggested -> confirmed / edited (their own verdict) / dismissed.
-- `stale` is set only by the fact-change trigger (0504) and cleared by deciding again. A new statement
-- version adds claims; old claims stay.

set role app_migrator;

create table app.witness_claims (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  document_id uuid not null references app.documents (id) on delete cascade,
  claim_index integer not null check (claim_index >= 0),
  text_verbatim bytea not null,
  word_ids text[] not null check (cardinality(word_ids) > 0),
  verdict text not null check (verdict in ('supports', 'contradicts', 'not_addressed')),
  fact_ids uuid[] not null default '{}',
  fact_versions integer[] not null default '{}',
  status text not null default 'suggested'
    check (status in ('suggested', 'confirmed', 'edited', 'dismissed', 'stale')),
  expert_verdict text check (expert_verdict in ('supports', 'contradicts', 'not_addressed')),
  job_id uuid references app.jobs (id) on delete set null,
  model_task text check (model_task ~ '^T-[0-9A-Za-z]+$'),
  prompt_version text check (length(prompt_version) <= 40),
  decided_by uuid,
  decided_at timestamptz,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (cardinality(fact_ids) = cardinality(fact_versions)),
  check (status <> 'edited' or expert_verdict is not null)
);
create index witness_claims_case_document on app.witness_claims (case_id, document_id, claim_index);
create index witness_claims_facts on app.witness_claims using gin (fact_ids);

create function app.witness_claims_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  marking_stale boolean := pg_trigger_depth() > 1;  -- only the fact-change trigger (0504) updates from inside a trigger
begin
  if tg_op = 'INSERT' then
    if new.status <> 'suggested' then
      perform app.refuse('a new claim starts suggested');
    end if;
    if not exists (select 1 from app.documents d where d.id = new.document_id and d.case_id = new.case_id) then
      perform app.refuse('a claim comes from a document of its own case');
    end if;
    new.version := 1;
    return new;
  end if;
  if new.case_id is distinct from old.case_id or new.document_id is distinct from old.document_id
     or new.claim_index is distinct from old.claim_index or new.text_verbatim is distinct from old.text_verbatim
     or new.word_ids is distinct from old.word_ids or new.verdict is distinct from old.verdict
     or new.fact_ids is distinct from old.fact_ids then
    perform app.refuse('a claim keeps the statement''s exact words and what it was compared with');
  end if;
  if new.status = 'stale' and old.status <> 'stale' and not marking_stale then
    perform app.refuse('only a change to a fact makes a claim stale');
  end if;
  if new.status is distinct from old.status and new.status <> 'stale'
     and not (old.status in ('suggested', 'stale') and new.status in ('confirmed', 'edited', 'dismissed')) then
    perform app.refuse(format('claim status cannot change from %s to %s', old.status, new.status));
  end if;
  if not marking_stale and new.status is distinct from old.status then
    new.fact_versions := coalesce(
      (select array_agg(coalesce(f.version, 0) order by i.n)
       from unnest(new.fact_ids) with ordinality as i (id, n)
       left join app.facts f on f.id = i.id),
      '{}');
    new.decided_by := app.current_person_id();
    new.decided_at := now();
  elsif new.fact_versions is distinct from old.fact_versions then
    perform app.refuse('a claim records the fact versions it was compared with');
  end if;
  if new.status <> 'edited' then
    new.expert_verdict := null;
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
revoke all on function app.witness_claims_guard() from public;
create trigger witness_claims_guard before insert or update on app.witness_claims
  for each row execute function app.witness_claims_guard();

revoke delete on app.witness_claims from app_api, app_worker;
revoke insert on app.witness_claims from app_api;

-- Access (rule 9) -------------------------------------------------------------------------------------------
alter table app.witness_claims enable row level security;

create policy witness_claims_view on app.witness_claims for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy witness_claims_decide on app.witness_claims for update to app_api
  using (app.can_access_case(case_id, 'opinion.write'))
  with check (app.can_access_case(case_id, 'opinion.write'));
create policy witness_claims_job on app.witness_claims to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

reset role;
