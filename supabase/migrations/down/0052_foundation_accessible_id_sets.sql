set role app_migrator;

-- Put the per-row form back on every policy this migration rewrote.
do $$
declare
  pol record;
  hit text[];
begin
  for pol in
    select schemaname, tablename, policyname, qual
    from pg_policies
    where schemaname = 'app' and cmd = 'SELECT' and 'app_api' = any (roles)
  loop
    hit := regexp_match(pol.qual, '^\(case_id IN \( SELECT app\.accessible_case_ids\(''([a-z_.]+)''::text\) AS accessible_case_ids\)\)$');
    if hit is not null then
      execute format('alter policy %I on %I.%I using (app.can_access_case(case_id, %L))',
                     pol.policyname, pol.schemaname, pol.tablename, hit[1]);
      continue;
    end if;
    hit := regexp_match(pol.qual, '^\(id IN \( SELECT app\.accessible_case_ids\(''([a-z_.]+)''::text\) AS accessible_case_ids\)\)$');
    if hit is not null then
      execute format('alter policy %I on %I.%I using (app.can_access_case(id, %L))',
                     pol.policyname, pol.schemaname, pol.tablename, hit[1]);
      continue;
    end if;
    hit := regexp_match(pol.qual, '^\(bundle_id IN \( SELECT app\.accessible_bundle_ids\(''([a-z_.]+)''::text\) AS accessible_bundle_ids\)\)$');
    if hit is not null then
      execute format('alter policy %I on %I.%I using (app.can_access_bundle(bundle_id, %L))',
                     pol.policyname, pol.schemaname, pol.tablename, hit[1]);
    end if;
  end loop;
end
$$;

drop function app.use_accessible_id_sets();
drop function app.accessible_bundle_ids(text);
drop function app.accessible_case_ids(text);

reset role;
