-- Down for 0000 foundation base. Drops the app schema (and everything in it) and the restricted roles.
drop schema if exists app cascade;
do $$
declare r text;
begin
  foreach r in array array['app_api', 'app_worker', 'app_migrator'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('drop owned by %I', r);
      execute format('drop role %I', r);
    end if;
  end loop;
end
$$;
