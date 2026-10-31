-- Down 0037: remove source tracking columns and restore the previous questions_guard.
set role app_migrator;

alter table app.questions drop column if exists source_instruction_version_id;
alter table app.questions drop column if exists source;

-- Restore the 0029 version of questions_guard (without source checks).
create or replace function app.questions_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
declare
  reopened boolean := false;
begin
  if tg_op = 'INSERT' then
    if new.status <> 'suggested' then
      perform app.refuse('a new question starts suggested');
    end if;
    new.version := 1;
    return new;
  end if;
  if new.case_id is distinct from old.case_id or new.text_verbatim is distinct from old.text_verbatim then
    perform app.refuse('a question keeps its exact words');
  end if;
  if old.status in ('confirmed', 'edited') and new.status = 'suggested'
     and new.instruction_version_id is distinct from old.instruction_version_id
     and not app.case_ever_served(old.case_id) then
    reopened := coalesce((select v.version_no from app.instruction_versions v where v.id = new.instruction_version_id)
                         > coalesce((select v.version_no from app.instruction_versions v
                                     where v.id = old.instruction_version_id), 0), false);
  end if;
  if new.status is distinct from old.status and not reopened
     and (old.status, new.status) not in (('suggested', 'confirmed'), ('suggested', 'edited')) then
    perform app.refuse(format('question status cannot change from %s to %s', old.status, new.status));
  end if;
  if old.status in ('confirmed', 'edited') and not reopened
     and (new.answer_parts is distinct from old.answer_parts
          or new.outside_expertise_source is distinct from old.outside_expertise_source) then
    perform app.refuse('a confirmed question keeps its answer parts and expertise source');
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;

reset role;
