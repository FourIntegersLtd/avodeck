-- 0043 foundation: update facts_guard for the bundle split.
--
-- "a fact cites a page of its own case" → "a fact cites a page of its case's bundle".
-- facts (0008) and pages/cases (0001) are all foundation tables, so this runs here.
--
-- The matching triggers on pipeline-owned tables (pipeline_same_case_page and its triggers on
-- page_analysis/ocr_words/page_embeddings/record_entries, plus page_quarantines_guard and
-- page_replacements_guard) move to the pipeline range instead (0128_pipeline_bundle_triggers.sql),
-- because those tables don't exist until pipeline migrations 0100+ run, which is after any
-- foundation-numbered migration.

set role app_migrator;

-- ── Update facts_guard: page must belong to the case's bundle ─────────────

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
    -- A fact cites a page of its case's bundle (not directly case_id on the page).
    if not exists (
      select 1 from app.pages p
        join app.cases c on c.bundle_id = p.bundle_id
      where p.id = new.page_id and c.id = new.case_id
    ) then
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
  -- Reassignment (0031): only from inside cases_named_expert_reset, for the case it names.
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
    ('verified', 'rejected'), ('edited', 'rejected'), ('rejected', 'verified'), ('verified', 'edited')
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
