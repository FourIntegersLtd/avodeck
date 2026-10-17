-- 0000 foundation: the database base (ADR 0013).
--
-- * `app` schema for every Avodeck table, not exposed through Supabase's Data API.
-- * Restricted roles: `app_api` (requests), `app_worker` (jobs), `app_migrator` (migrations only).
--   None is a superuser, none bypasses row-level security, the runtime roles own nothing.
--   Roles are created NOLOGIN; each cell's operator enables LOGIN with a secret password
--   (locally: supabase/seed.sql). No password is ever written in a migration.
-- * Session helpers read the per-transaction settings that core/db.py sets with SET LOCAL.
--   With nothing set they return NULL / empty, so RLS policies fail closed.

-- Roles ---------------------------------------------------------------------------------------
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'app_migrator') then
    create role app_migrator nologin nosuperuser nobypassrls nocreatedb nocreaterole;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'app_api') then
    create role app_api nologin nosuperuser nobypassrls nocreatedb nocreaterole;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'app_worker') then
    create role app_worker nologin nosuperuser nobypassrls nocreatedb nocreaterole;
  end if;
end
$$;

-- The local migration runner (postgres) acts as app_migrator so objects it creates can be
-- handed over and default privileges below apply either way.
grant app_migrator to postgres;

-- Schema --------------------------------------------------------------------------------------
create schema if not exists app authorization app_migrator;
revoke all on schema app from public;
grant usage on schema app to app_api, app_worker;

-- Every table, sequence and function created later in `app` (by app_migrator or by the local
-- runner) is usable by the runtime roles; RLS on each table decides which rows they see.
alter default privileges for role app_migrator in schema app
  grant select, insert, update, delete on tables to app_api, app_worker;
alter default privileges for role app_migrator in schema app
  grant usage, select on sequences to app_api, app_worker;
alter default privileges for role postgres in schema app
  grant select, insert, update, delete on tables to app_api, app_worker;
alter default privileges for role postgres in schema app
  grant usage, select on sequences to app_api, app_worker;
alter default privileges for role app_migrator in schema app revoke execute on functions from public;
alter default privileges for role postgres in schema app revoke execute on functions from public;
alter default privileges for role app_migrator in schema app grant execute on functions to app_api, app_worker;
alter default privileges for role postgres in schema app grant execute on functions to app_api, app_worker;

-- Session helpers -----------------------------------------------------------------------------
create or replace function app.current_person_id() returns uuid
  language sql stable parallel safe
  set search_path = ''
as $$ select nullif(current_setting('app.person_id', true), '')::uuid $$;

create or replace function app.current_org_ids() returns uuid[]
  language sql stable parallel safe
  set search_path = ''
as $$
  select coalesce(string_to_array(nullif(current_setting('app.org_ids', true), ''), ',')::uuid[], '{}'::uuid[])
$$;

create or replace function app.current_job_case_id() returns uuid
  language sql stable parallel safe
  set search_path = ''
as $$ select nullif(current_setting('app.job_case_id', true), '')::uuid $$;

alter function app.current_person_id() owner to app_migrator;
alter function app.current_org_ids() owner to app_migrator;
alter function app.current_job_case_id() owner to app_migrator;
revoke execute on function app.current_person_id(), app.current_org_ids(), app.current_job_case_id() from public;
grant execute on function app.current_person_id(), app.current_org_ids(), app.current_job_case_id()
  to app_api, app_worker;
