-- 0037 foundation: track which letter each question was found in.
-- * `questions.source_instruction_version_id`: the instruction version this question was extracted from,
--   set on insert and never changed. Re-pointing (`instruction_version_id`) still happens on a new letter
--   so the reopen guard works, but source stays fixed for provenance (page links, word ids).
-- * `questions.source`: who found this question — 'letter' (the AI extraction) or 'expert' (added by the doctor).
-- Both are immutable after insert. RLS is the table's own (0003).

set role app_migrator;

alter table app.questions
  add column source_instruction_version_id uuid references app.instruction_versions (id),
  add column source text not null default 'letter' check (source in ('letter', 'expert'));

-- Backfill: existing questions get their current instruction_version_id as source.
update app.questions set source_instruction_version_id = instruction_version_id
  where source_instruction_version_id is null;

-- Same for assumptions (they also need the source version for page links).
alter table app.assumptions
  add column source_instruction_version_id uuid references app.instruction_versions (id);

update app.assumptions set source_instruction_version_id = instruction_version_id
  where source_instruction_version_id is null;

-- Update the questions guard to refuse changes to the new columns.
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
  if new.source_instruction_version_id is distinct from old.source_instruction_version_id then
    perform app.refuse('a question keeps its source letter');
  end if;
  if new.source is distinct from old.source then
    perform app.refuse('a question keeps its source');
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
