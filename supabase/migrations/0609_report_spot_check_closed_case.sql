-- 0609 report: a spot-check link stops opening once its case is withdrawn or deleted, or the report version it
-- was built from is withdrawn (audit AUDIT-11 NEW-3). The colleague sees the same 'expired' outcome as for an
-- old link, so nothing says why, and it is not counted as a wrong code. Settled and archived cases still open:
-- a finished case can still be spot-checked. Signatures, search_path and grants are as in 0604.

set role app_migrator;

create or replace function app.spot_check_unlock(p_token_hash text, p_code_hash text)
  returns table (spot_check_id uuid, spot_case_id uuid, outcome text, spot_expires_at timestamptz,
                 spot_fact_ids uuid[], responded boolean)
  language plpgsql
  volatile
  security definer
  set search_path = ''
as $$
declare
  s app.spot_checks%rowtype;
  v_outcome text;
  v_closed boolean;
begin
  select * into s from app.spot_checks c where c.token_hash = p_token_hash for update;
  if not found then
    return query select null::uuid, null::uuid, 'unknown'::text, null::timestamptz, null::uuid[], false;
    return;
  end if;
  select (c.status in ('withdrawn', 'deleted') or coalesce(v.status = 'withdrawn', false))
    into v_closed
    from app.cases c
    left join app.report_versions v on v.id = s.report_version_id
    where c.id = s.case_id;
  if coalesce(v_closed, false) then
    v_outcome := 'expired';
  elsif s.expires_at <= now() then
    v_outcome := 'expired';
  elsif s.code_attempts >= 5 then
    v_outcome := 'locked';
  elsif s.code_hash is distinct from p_code_hash then
    v_outcome := 'bad_code';
    update app.spot_checks c set code_attempts = c.code_attempts + 1 where c.id = s.id;
  else
    v_outcome := 'ok';
    update app.spot_checks c set verified_at = coalesce(c.verified_at, now()) where c.id = s.id;
  end if;
  insert into app.audit_log (person_id, case_id, action, target)
    values (null, s.case_id, case when v_outcome = 'ok' then 'spot_check.opened' else 'spot_check.refused' end,
            'spot_check:' || s.id);
  return query select s.id, s.case_id, v_outcome, s.expires_at, s.fact_ids, s.responded_at is not null;
end
$$;
revoke all on function app.spot_check_unlock(text, text) from public;
grant execute on function app.spot_check_unlock(text, text) to app_api;

create or replace function app.spot_check_respond(p_token_hash text, p_code_hash text, p_responses jsonb)
  returns text
  language plpgsql
  volatile
  security definer
  set search_path = ''
as $$
declare
  s app.spot_checks%rowtype;
  v_outcome text;
  v_closed boolean;
  v_ids uuid[];
begin
  select * into s from app.spot_checks c where c.token_hash = p_token_hash for update;
  if not found then
    return 'unknown';
  end if;
  select (c.status in ('withdrawn', 'deleted') or coalesce(v.status = 'withdrawn', false))
    into v_closed
    from app.cases c
    left join app.report_versions v on v.id = s.report_version_id
    where c.id = s.case_id;
  if coalesce(v_closed, false) then
    v_outcome := 'expired';
  elsif s.expires_at <= now() then
    v_outcome := 'expired';
  elsif s.code_attempts >= 5 then
    v_outcome := 'locked';
  elsif s.code_hash is distinct from p_code_hash then
    v_outcome := 'bad_code';
    update app.spot_checks c set code_attempts = c.code_attempts + 1 where c.id = s.id;
  elsif s.responded_at is not null then
    v_outcome := 'already';
  elsif jsonb_typeof(p_responses) <> 'array' then
    v_outcome := 'invalid';
  else
    select array_agg((e ->> 'fact_id')::uuid) into v_ids from jsonb_array_elements(p_responses) e;
    if v_ids is null or cardinality(v_ids) <> cardinality(s.fact_ids)
       or (select count(distinct x) from unnest(v_ids) x) <> cardinality(s.fact_ids)
       or not (v_ids <@ s.fact_ids) then
      v_outcome := 'invalid';
    else
      v_outcome := 'ok';
      update app.spot_checks c set responses = p_responses, responded_at = now() where c.id = s.id;
    end if;
  end if;
  if v_outcome <> 'invalid' then
    insert into app.audit_log (person_id, case_id, action, target)
      values (null, s.case_id,
              case when v_outcome = 'ok' then 'spot_check.answered' else 'spot_check.refused' end,
              'spot_check:' || s.id);
  end if;
  return v_outcome;
end
$$;
revoke all on function app.spot_check_respond(text, text, jsonb) from public;
grant execute on function app.spot_check_respond(text, text, jsonb) to app_api;

reset role;
