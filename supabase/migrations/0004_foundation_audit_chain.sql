-- 0004 foundation: the audit log hash chain (architecture 10.1, 10.9).
--
-- Every insert takes one transaction-scoped lock, numbers the row, links it to the previous
-- row's hash and hashes its own fields, so the chain is in `seq` order even under concurrent
-- writers. The running app cannot update or delete rows (grants revoked in 0001);
-- core/audit.py `find_breaks()` recomputes the chain and names any row that was changed,
-- removed or inserted out of order. The field encoding here and in core/audit.py must match.

set role app_migrator;

create function app.audit_hash(
  seq bigint, person_id uuid, organisation_id uuid, case_id uuid, action text, target text,
  at timestamptz, prev_hash text
) returns text
  language sql immutable parallel safe
  set search_path = ''
as $$
  select encode(sha256(convert_to(concat_ws(chr(31),
    seq::text,
    coalesce(person_id::text, ''),
    coalesce(organisation_id::text, ''),
    coalesce(case_id::text, ''),
    action,
    target,
    ((extract(epoch from at) * 1000000)::bigint)::text,
    prev_hash), 'UTF8')), 'hex')
$$;

create function app.audit_log_chain() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  previous text;
begin
  perform pg_advisory_xact_lock(7410001);  -- one writer at a time; released at commit
  new.seq := nextval('app.audit_log_seq');
  new.at := coalesce(new.at, now());
  select l.hash into previous from app.audit_log l order by l.seq desc limit 1;
  new.prev_hash := coalesce(previous, repeat('0', 64));
  new.hash := app.audit_hash(new.seq, new.person_id, new.organisation_id, new.case_id, new.action, new.target,
                             new.at, new.prev_hash);
  return new;
end
$$;

create trigger audit_log_chain before insert on app.audit_log
  for each row execute function app.audit_log_chain();

reset role;
