-- 9009 foundation: the run-time check of the three hash chains (audit 16, PV-13c). It runs after every slice
-- migration because it calls `app.page_reads_chain_ok` and `app.fact_verifications_chain_ok` (02xx).
--
-- Each call checks the next `p_budget` rows (audit log) or cases (reader chains) after the cursor in `app.chain_checks`,
-- and starts again from the beginning once it reaches the end, so a full pass is spread over many housekeeping rounds.
-- It returns only ids: `seq` numbers of broken audit rows, and (chain name, case id) of broken case chains. The worker
-- (`app.core.chain_check`) alone may call it.

set role app_migrator;

create function app.verify_audit_chain(p_budget integer)
  returns table (checked integer, broken bigint[])
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  v_seq bigint;
  v_hash text;
  v_count integer;
  v_last_seq bigint;
  v_last_hash text;
  v_broken bigint[];
begin
  if p_budget <= 0 or not pg_try_advisory_xact_lock(7410004) then  -- one checker at a time
    return query select 0, '{}'::bigint[];
    return;
  end if;
  insert into app.chain_checks (name) values ('audit_log') on conflict do nothing;
  select c.last_seq, c.last_hash into v_seq, v_hash from app.chain_checks c where c.name = 'audit_log';

  with slice as (
    select l.* from app.audit_log l where l.seq > v_seq order by l.seq limit p_budget
  ), judged as (
    select s.seq, s.hash,
      (s.hash = app.audit_hash(s.seq, s.person_id, s.organisation_id, s.case_id, s.action, s.target, s.at, s.prev_hash)
       and s.prev_hash = coalesce(lag(s.hash) over (order by s.seq), v_hash, repeat('0', 64))) as ok
    from slice s
  )
  select count(*)::integer,
         max(j.seq),
         (array_agg(j.hash order by j.seq desc))[1],
         coalesce(array_agg(j.seq order by j.seq) filter (where not j.ok), '{}')
    into v_count, v_last_seq, v_last_hash, v_broken
    from judged j;

  if v_count < p_budget then  -- reached the end: the next call starts the log again
    update app.chain_checks c set last_seq = 0, last_hash = null, updated_at = now(),
      breaks = c.breaks + cardinality(v_broken),
      last_break_at = case when cardinality(v_broken) > 0 then now() else c.last_break_at end
      where c.name = 'audit_log';
  else
    update app.chain_checks c set last_seq = v_last_seq, last_hash = v_last_hash, updated_at = now(),
      breaks = c.breaks + cardinality(v_broken),
      last_break_at = case when cardinality(v_broken) > 0 then now() else c.last_break_at end
      where c.name = 'audit_log';
  end if;
  return query select v_count, v_broken;
end
$$;

create function app.verify_case_chains(p_budget integer)
  returns table (r_chain text, r_case uuid, r_ok boolean)
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  v_last uuid;
  v_id uuid;
  v_n integer := 0;
  v_bad integer := 0;
begin
  if p_budget <= 0 or not pg_try_advisory_xact_lock(7410005) then
    return;
  end if;
  insert into app.chain_checks (name) values ('case_chains') on conflict do nothing;
  select c.last_case into v_last from app.chain_checks c where c.name = 'case_chains';

  for v_id in select k.id from app.cases k where v_last is null or k.id > v_last order by k.id limit p_budget loop
    v_n := v_n + 1;
    v_last := v_id;
    r_case := v_id;
    r_chain := 'page_reads';
    r_ok := app.page_reads_chain_ok(v_id);
    if not r_ok then v_bad := v_bad + 1; end if;
    return next;
    r_chain := 'fact_verifications';
    r_ok := app.fact_verifications_chain_ok(v_id);
    if not r_ok then v_bad := v_bad + 1; end if;
    return next;
  end loop;

  update app.chain_checks c set
    last_case = case when v_n < p_budget then null else v_last end,  -- the end reached: start again next call
    breaks = c.breaks + v_bad,
    last_break_at = case when v_bad > 0 then now() else c.last_break_at end,
    updated_at = now()
    where c.name = 'case_chains';
end
$$;

revoke all on function app.verify_audit_chain(integer), app.verify_case_chains(integer) from public;
grant execute on function app.verify_audit_chain(integer), app.verify_case_chains(integer) to app_worker;

reset role;
