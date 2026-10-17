-- Down for 0003 foundation case access: policies, then the helpers.
do $$
declare p record;
begin
  for p in select policyname, tablename from pg_policies where schemaname = 'app' loop
    execute format('drop policy %I on app.%I', p.policyname, p.tablename);
  end loop;
end
$$;
drop function if exists app.ensure_person(text, text, text), app.my_org_ids(),
  app.can_access_case(uuid, text), app.case_access(uuid), app.case_action_allowed(text, text, text, text);
