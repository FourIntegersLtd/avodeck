-- 0504 write (slice E): a point or witness claim becomes `stale` when a fact under it changes (verified,
-- edited, rejected or re-verified: every change raises the fact's version), until the expert decides
-- again (state-machines.md). Runs after slice B's change to app.facts and writes only slice E's tables;
-- the guards in 0500-0501 accept `stale` only from inside this trigger.

set role app_migrator;

create function app.write_mark_stale() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  update app.points set status = 'stale'
   where fact_id = new.id and status <> 'stale';
  update app.witness_claims set status = 'stale'
   where case_id = new.case_id and new.id = any (fact_ids) and status <> 'stale';
  return null;
end
$$;
revoke all on function app.write_mark_stale() from public;
create trigger write_mark_stale after update on app.facts
  for each row when (new.version is distinct from old.version)
  execute function app.write_mark_stale();

reset role;
