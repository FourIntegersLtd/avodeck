-- Undo 0033: cases_creator_joins as in 0019; drop the replacement function and table.
set role app_migrator;

drop function app.create_replacement_case(uuid, uuid, text, boolean);
drop table app.case_replacements;

create or replace function app.cases_creator_joins() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  me constant uuid := app.current_person_id();
begin
  if me is not null and me is distinct from new.named_expert_id then
    insert into app.case_members (case_id, person_id, case_role, added_by) values (new.id, me, 'assistant', me);
  end if;
  return new;
end
$$;

reset role;
