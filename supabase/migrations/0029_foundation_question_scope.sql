-- 0029 foundation: what a question asks the expert to answer.
-- * `questions.answer_parts`: the parts of the question the expert answers (keys from the case type's
--   question-type `parts`, e.g. timing only); null means the whole question. The question's exact words
--   never change (0008); this only narrows what is answered.
-- * `questions.outside_expertise_source`: who says the question is outside the expert's expertise,
--   the pack (the specialty marks the type) or the expert; null when it is not.
-- Both can change while the question is suggested and are locked once it is confirmed or edited (like
-- its wording). A newer instruction version reopens the question (back to suggested, 0019) and they can
-- change again. RLS is the table's own (0003).

set role app_migrator;

alter table app.questions
  add column answer_parts text[],
  add column outside_expertise_source text,
  add constraint questions_answer_parts_some check (answer_parts is null or cardinality(answer_parts) > 0),
  add constraint questions_outside_expertise_source check (outside_expertise_source in ('pack', 'expert'));

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
