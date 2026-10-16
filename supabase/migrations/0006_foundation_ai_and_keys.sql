-- 0006 foundation: AI call log, per-case keys and sealed session tokens (architecture 4.11, 6, 9; ADR 0010).
--
-- * `ai_calls`: one row per model call, metadata only (task, model ID, region, prompt version,
--   token counts, outcome). No column can hold a prompt or a response (rule 6). Append-only.
-- * `case_keys`: each case's data key, wrapped by the cell's key provider (local stand-in now,
--   KMS later). The plain key never reaches the database. Destroying a case's key (case deletion)
--   is a worker-only function that nulls the wrapped key and writes an audit row.
-- * `app.ai_budget`: what the AI kit needs for the cost guard (F11): tokens used so far on a case
--   and its page count. It also refuses anyone without access, before any model is called.
-- * `sessions`: Supabase Auth tokens are stored sealed by core/crypto.py (`av1:` prefix) only.

set role app_migrator;

create table app.ai_calls (
  id uuid primary key default gen_random_uuid(),
  case_id uuid references app.cases (id),
  person_id uuid,
  task_id text not null,
  model_id text not null,
  region text not null,
  prompt_version text not null,
  input_tokens integer not null check (input_tokens >= 0),
  output_tokens integer not null check (output_tokens >= 0),
  outcome text not null check (outcome in ('ok', 'invalid_output')),
  at timestamptz not null default now()
);
create index ai_calls_case on app.ai_calls (case_id);

create table app.case_keys (
  case_id uuid primary key references app.cases (id),
  wrapped_key bytea,
  key_ref text not null,
  created_at timestamptz not null default now(),
  destroyed_at timestamptz,
  check ((wrapped_key is null) = (destroyed_at is not null))
);

alter table app.ai_calls enable row level security;
alter table app.case_keys enable row level security;
revoke update, delete on app.ai_calls, app.case_keys from app_api, app_worker;

create policy ai_calls_view on app.ai_calls for select to app_api
  using (case when case_id is null then person_id = app.current_person_id()
              else app.can_access_case(case_id, 'case.view_content') end);
create policy ai_calls_add on app.ai_calls for insert to app_api
  with check (person_id = app.current_person_id()
              and (case_id is null or app.can_access_case(case_id, 'case.view_content')));
create policy ai_calls_job on app.ai_calls to app_worker
  using (case_id = app.current_job_case_id())
  with check (case_id = app.current_job_case_id() and person_id is null);

create policy case_keys_view on app.case_keys for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy case_keys_add on app.case_keys for insert to app_api
  with check (app.can_access_case(case_id, 'case.view_content'));
create policy case_keys_job on app.case_keys to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create function app.ai_budget(p_case_id uuid)
  returns table (tokens_used bigint, pages bigint)
  language plpgsql
  stable
  security definer
  set search_path = ''
as $$
begin
  if not (coalesce(app.current_job_case_id() = p_case_id, false)
          or coalesce(app.can_access_case(p_case_id, 'case.view_content'), false)) then
    raise exception using errcode = '42501', message = 'no access to this case';
  end if;
  return query
  select (select coalesce(sum(c.input_tokens + c.output_tokens), 0)::bigint from app.ai_calls c where c.case_id = p_case_id),
         (select count(*) from app.pages p where p.case_id = p_case_id);
end
$$;

create function app.destroy_case_key(p_case_id uuid) returns void
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if app.current_job_case_id() is distinct from p_case_id then
    raise exception using errcode = '42501', message = 'only the case''s own job may destroy its key';
  end if;
  update app.case_keys set wrapped_key = null, destroyed_at = now()
  where case_id = p_case_id and destroyed_at is null;
  insert into app.audit_log (case_id, action, target) values (p_case_id, 'case.key_destroyed', 'case:' || p_case_id::text);
end
$$;

revoke execute on function app.ai_budget(uuid), app.destroy_case_key(uuid) from public, app_api, app_worker;
grant execute on function app.ai_budget(uuid) to app_api, app_worker;
grant execute on function app.destroy_case_key(uuid) to app_worker;

-- Tokens stored before this migration were plaintext: drop them (those sessions sign in again
-- when their Supabase token expires) and refuse plaintext from now on.
update app.sessions set auth_access_token = null, auth_refresh_token = null
where auth_access_token not like 'av1:%' or auth_refresh_token not like 'av1:%';
alter table app.sessions
  add constraint sessions_auth_tokens_sealed check (
    (auth_access_token is null or auth_access_token like 'av1:%')
    and (auth_refresh_token is null or auth_refresh_token like 'av1:%'));

reset role;
