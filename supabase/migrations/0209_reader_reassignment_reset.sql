-- 0209 reader [foundation change in slice B's range, scenario S3; founder-approved, docs/slices/0-foundation.md]:
-- a new named expert starts with every fact a suggestion (migration 0031, `cases_named_expert_reset`).
-- It must run after 0203 and 0208, which it changes, so it is numbered here:
-- * `facts_guard` [foundation path; whole body from 0208, unchanged but for one exception]: checked ->
--   suggested only from inside `cases_named_expert_reset` (the marker `app.fact_checks_reset` names the
--   fact's case and the trigger depth is > 1); the version rises.
-- * `facts_guard` also holds the case row `for share` whenever a fact's status changes to a checked one,
--   so a reassignment in flight waits for the check (and the reset then sees it), or the check waits for
--   the reassignment and is refused (42501) when the running app's person is no longer the named expert.
-- * `fact_verifications`: a new action `reset`, written with no person (like `remapped`); the hash already
--   covers the action and reads a missing person as ''.
-- A later slice B migration that replaces `facts_guard` carries this exception.

set role app_migrator;

alter table app.fact_verifications drop constraint fact_verifications_action_check;
alter table app.fact_verifications add constraint fact_verifications_action_check
  check (action in ('correct', 'not_right', 'edit', 'move', 'remapped', 'reset'));
alter table app.fact_verifications drop constraint fact_verifications_person;
alter table app.fact_verifications add constraint fact_verifications_person
  check (person_id is not null or action in ('remapped', 'reset'));

create or replace function app.facts_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.status <> 'suggested' then
      perform app.refuse('a new fact starts suggested');
    end if;
    if not exists (select 1 from app.pages p where p.id = new.page_id and p.case_id = new.case_id) then
      perform app.refuse('a fact cites a page of its own case');
    end if;
    new.version := 1;
    return new;
  end if;
  if new.case_id is distinct from old.case_id then
    perform app.refuse('a fact stays on its page');
  end if;
  if new.page_id is distinct from old.page_id and not exists (
    select 1 from app.pages p where p.id = old.page_id and p.superseded_by_page_id = new.page_id
  ) then
    perform app.refuse('a fact stays on its page');
  end if;
  -- Reassignment (0031): only from inside `cases_named_expert_reset`, for the case it names.
  if new.status = 'suggested' and old.status <> 'suggested'
     and current_setting('app.fact_checks_reset', true) = new.case_id::text and pg_trigger_depth() > 1 then
    new.version := old.version + 1;
    new.updated_at := now();
    return new;
  end if;
  -- A check and a reassignment never cross (0031): hold the case row while the check is made.
  if new.status is distinct from old.status and new.status <> 'suggested' then
    if (select c.named_expert_id from app.cases c where c.id = new.case_id for share)
         is distinct from app.current_person_id()
       and session_user = 'app_api' then
      raise exception using errcode = '42501', message = 'only the case''s named expert checks its facts';
    end if;
  end if;
  if new.status is distinct from old.status and (old.status, new.status) not in (
    ('suggested', 'verified'), ('suggested', 'edited'), ('suggested', 'rejected'),
    ('verified', 'rejected'), ('edited', 'rejected'), ('rejected', 'verified')
  ) and not (
    -- "Please check" after a re-read: the expert changes the fact to cite the words there now.
    (old.status, new.status) = ('verified', 'edited')
    and exists (select 1 from app.fact_flags g where g.fact_id = new.id and g.flagged_for = 'recheck')
  ) then
    perform app.refuse(format('fact status cannot change from %s to %s', old.status, new.status));
  end if;
  -- app.set_fact_availability() alone may change when an entry reached the clinician without a new version.
  if current_setting('app.fact_availability_write', true) = new.id::text
     and (to_jsonb(new) - '{available_at,availability_basis}'::text[])
         = (to_jsonb(old) - '{available_at,availability_basis}'::text[]) then
    return new;
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;

reset role;
