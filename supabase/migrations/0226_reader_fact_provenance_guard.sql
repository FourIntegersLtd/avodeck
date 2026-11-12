-- 0226 reader: the database holds the provenance rules (security audit 16, PV-01 and PV-02).
--
-- Until now "a fact cites words of its own page" and "a checked fact changes only through a check" were
-- enforced by the application alone. A deferred constraint trigger (it runs at commit, so the words, the fact
-- and the log row may be written in any order inside one transaction) now repeats both for the two restricted
-- roles the application connects as (rule 9 and ADR 0013; the owner/admin role used by migrations and test
-- fixtures is not checked):
--
-- PV-01  every id in `facts.word_ids` is a word of the fact's own page in `app.ocr_words` (OCR words, the
--        expert-typed `e{n}` words and re-read words are all rows there), on insert and whenever the cited
--        words or the page change (the superseded-page move cites the clearer copy's words).
--        The value itself is sealed (ADR 0032), so the database cannot check that the words show the value;
--        that check stays in the application (`provenance.supported`).
-- PV-02  a fact that is checked (`verified` or `edited`) changes only with a `fact_verifications` row for the same
--        fact, in the same transaction, saying what changed. A row is required when the fact moves INTO or OUT OF
--        the checked statuses (checking it, quarantine and its undo, the reassignment reset), and when a checked
--        fact's value, details, cited words, date (`event_date`) or how the date was read (`date_basis`) change.
--        The row must carry BOTH sides of each thing that changed: its old value, details, words and date must be
--        what the fact had (`old.*`) and its new ones what the fact has now, so the earlier value survives in the
--        log (rule 2) and a row that leaves the old side empty is refused. A status-only move is matched on the
--        value (old and new). Every legitimate writer already logs: the check routes, the page set-aside and its
--        undo (`page_set_aside` / `quarantine_undone`), the reassignment reset (`reset`), the re-read re-map and the
--        date repair (both `remapped`, the repair with its old and new date). A rejected fact's value may be purged
--        (rejected is not checked).
-- PV-03  covered by the same rule: a change of date or of its basis on a checked fact needs a row with both dates.

set role app_migrator;

create function app.facts_provenance_check() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  cur app.facts;
  was_checked boolean;
  is_checked boolean;
  status_moved boolean;
  value_moved boolean;
  fields_moved boolean;
  words_moved boolean;
  date_moved boolean;
begin
  if session_user not in ('app_api', 'app_worker') then
    return null;
  end if;
  select * into cur from app.facts f where f.id = new.id;
  if not found then
    return null;  -- deleted again in the same transaction
  end if;
  if (tg_op = 'INSERT' or cur.word_ids is distinct from old.word_ids or cur.page_id is distinct from old.page_id)
     and exists (
       select 1 from unnest(coalesce(cur.word_ids, '{}'::text[])) w
       where not exists (select 1 from app.ocr_words o where o.page_id = cur.page_id and o.word_id = w)
     ) then
    perform app.refuse('a fact cites words of its own page');
  end if;
  if tg_op <> 'UPDATE' then
    return null;
  end if;
  was_checked := old.status in ('verified', 'edited');
  is_checked := cur.status in ('verified', 'edited');
  if not (was_checked or is_checked) then
    return null;  -- a suggestion or a rejected fact: nothing checked changes
  end if;
  status_moved := was_checked <> is_checked;
  value_moved := cur.value is distinct from old.value;
  fields_moved := cur.fields is distinct from old.fields;
  words_moved := cur.word_ids is distinct from old.word_ids;
  date_moved := cur.event_date is distinct from old.event_date or cur.date_basis is distinct from old.date_basis;
  if not (status_moved or value_moved or fields_moved or words_moved or date_moved) then
    return null;
  end if;
  if not exists (
    select 1 from app.fact_verifications v
    where v.fact_id = cur.id and v.at = now()
      and (not (status_moved or value_moved)
           or (v.old_value is not distinct from old.value and v.new_value is not distinct from cur.value))
      and (not fields_moved
           or (v.old_fields is not distinct from old.fields and v.new_fields is not distinct from cur.fields))
      and (not words_moved
           or (v.old_word_ids is not distinct from old.word_ids and v.new_word_ids is not distinct from cur.word_ids))
      and (not date_moved
           or (v.old_event_date is not distinct from old.event_date
               and v.new_event_date is not distinct from cur.event_date
               and v.old_date_basis is not distinct from old.date_basis
               and v.new_date_basis is not distinct from cur.date_basis))
  ) then
    perform app.refuse('a checked fact changes only with a row in the check log');
  end if;
  return null;
end
$$;

create constraint trigger facts_provenance
  after insert or update of word_ids, page_id, value, fields, status, event_date, date_basis on app.facts
  deferrable initially deferred
  for each row execute function app.facts_provenance_check();

reset role;
