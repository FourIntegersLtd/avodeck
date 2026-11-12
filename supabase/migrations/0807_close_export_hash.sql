-- 0807 close: the SHA-256 of a finished export archive is recorded when it is built (PV-17b).
--
-- The worker writes an audit row `case.export_built`, target `export:<job id>:<variant>:<sha256>` (ids, a short
-- code and a hash; no patient detail). The audit log is append-only and hash-chained and outlives the case, so
-- the record cannot be changed with the stored copy. The download route reads the hash through this function,
-- for whoever may export the case, and refuses a copy that does not match it.

set role app_migrator;

create function app.close_export_hash(p_case_id uuid, p_job_id uuid, p_variant text) returns text
  language plpgsql stable
  security definer
  set search_path = ''
as $$
begin
  if not app.can_access_case(p_case_id, 'case.export') then
    raise exception using errcode = '42501', message = 'this needs case.export';
  end if;
  return (
    select split_part(a.target, ':', 4)
    from app.audit_log a
    where a.case_id = p_case_id and a.action = 'case.export_built'
      and a.target like 'export:' || p_job_id::text || ':' || p_variant || ':%'
    order by a.seq desc limit 1
  );
end
$$;

revoke all on function app.close_export_hash(uuid, uuid, text) from public, app_api, app_worker;
grant execute on function app.close_export_hash(uuid, uuid, text) to app_api;

reset role;
