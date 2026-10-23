-- 0031 foundation: a new named expert starts with no checked facts (scenario S3, architecture 10.1
-- "Reassignment": reads and verifications are personal).
--
-- Reads are already personal: only the named expert's reads count for citing (`citable_facts()`).
-- Checks live on the fact (`facts.status`), which every slice reads, so when `cases.named_expert_id`
-- changes (only before serving: `cases_actor`, 0012) the trigger `cases_named_expert_reset` puts every
-- checked fact of the case (verified, edited, rejected) back to `suggested`:
-- * an edited fact gets back the suggestion it had before the latest expert's first change (value,
--   fields and cited words from that `fact_verifications` row, and the box around those words), unless
--   its page was read again (`remapped`) or it moved to a clearer copy (`move`) since then: the old
--   words may be gone, so it keeps the words it cites now;
-- * one `fact_verifications` row per fact, action `reset`, no person (allowed by 0209), so the log and
--   its hash chain stay whole; the version rises, so an ETag from before is stale;
-- * the fact's "Please check" flag (`fact_flags.flagged_for = 'recheck'`) goes with the check it asked
--   about.
-- `facts_guard` (redefined in 0209, after slice B's 0208) lets checked -> suggested through only from
-- inside this trigger: the marker `app.fact_checks_reset` names the case and the trigger depth is > 1,
-- so setting the marker by hand opens nothing.
-- The trigger refuses (AV409) a case that has ever been served, whoever changes it: `cases_actor`
-- checks only the running app. A check in flight and a reassignment wait for each other: `facts_guard`
-- (0209) holds the case row `for share` while a fact is checked, which the reassignment's row update waits
-- for, and refuses the check if the case's named expert changed meanwhile.
-- app.word_box(page, word_ids)   the words' box as a share of the page, as `document_words.box()` and
--                                slice B's `repo.word_box()` draw it; null when no word has a place on
--                                the picture. Only the reset (its owner) calls it; no runtime role may.
-- Function bodies are plpgsql: the tables they read (`fact_verifications`, `fact_flags`, `ocr_words`,
-- `page_analysis`) are created by slices A and B in later migrations.

set role app_migrator;

create function app.word_box(p_page uuid, p_word_ids text[]) returns jsonb
  language plpgsql stable
  security definer
  set search_path = ''
as $$
declare
  w double precision;
  h double precision;
  b record;
begin
  select coalesce(nullif(a.width, 0), 0), coalesce(nullif(a.height, 0), 0) into w, h
  from app.page_analysis a where a.page_id = p_page;
  if coalesce(w, 0) = 0 then
    select max(v) into w from app.ocr_words o, unnest(o.polygon) with ordinality as p(v, i)
    where o.page_id = p_page and i % 2 = 1;
  end if;
  if coalesce(h, 0) = 0 then
    select max(v) into h from app.ocr_words o, unnest(o.polygon) with ordinality as p(v, i)
    where o.page_id = p_page and i % 2 = 0;
  end if;
  w := coalesce(nullif(w, 0), 1);
  h := coalesce(nullif(h, 0), 1);
  select min(v) filter (where i % 2 = 1) as x0, max(v) filter (where i % 2 = 1) as x1,
         min(v) filter (where i % 2 = 0) as y0, max(v) filter (where i % 2 = 0) as y1,
         bool_or(v <> 0) as placed
  into b
  from app.ocr_words o, unnest(o.polygon::double precision[]) with ordinality as p(v, i)
  where o.page_id = p_page and o.word_id = any (p_word_ids);
  if b.placed is not true then
    return null;
  end if;
  return jsonb_build_object(
    'x0', round(least(1, greatest(0, b.x0 / w))::numeric, 5)::double precision,
    'y0', round(least(1, greatest(0, b.y0 / h))::numeric, 5)::double precision,
    'x1', round(least(1, greatest(0, b.x1 / w))::numeric, 5)::double precision,
    'y1', round(least(1, greatest(0, b.y1 / h))::numeric, 5)::double precision);
end
$$;

create function app.cases_named_expert_reset() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  f record;
  last_reset bigint;
  edit_seq bigint;
  s_value text;
  s_fields jsonb;
  s_words text[];
begin
  if app.case_ever_served(new.id) then
    perform app.refuse('the named expert cannot change after serving');
  end if;
  perform set_config('app.fact_checks_reset', new.id::text, true);
  for f in select * from app.facts where case_id = new.id and status <> 'suggested' order by created_at, id
  loop
    select coalesce(max(r.seq), -1) into last_reset
    from app.fact_verifications r where r.fact_id = f.id and r.action = 'reset';
    edit_seq := null;
    select r.seq, r.old_value, coalesce(r.old_fields, f.fields), r.old_word_ids into edit_seq, s_value, s_fields, s_words
    from app.fact_verifications r
    where r.fact_id = f.id and r.action = 'edit' and r.seq > last_reset and r.old_word_ids is not null
    order by r.seq limit 1;
    if edit_seq is null or exists (
      select 1 from app.fact_verifications r
      where r.fact_id = f.id and r.seq > edit_seq and r.action in ('remapped', 'move')) then
      s_value := f.value;
      s_fields := f.fields;
      s_words := f.word_ids;
    end if;
    update app.facts set
      status = 'suggested',
      value = s_value,
      fields = s_fields,
      word_ids = s_words,
      bbox = case when s_words = f.word_ids then f.bbox else app.word_box(f.page_id, s_words) end
    where id = f.id;
    insert into app.fact_verifications (case_id, fact_id, person_id, action, old_value, new_value, old_fields,
                                        new_fields, old_word_ids, new_word_ids)
    values (f.case_id, f.id, null, 'reset', f.value, s_value, f.fields, s_fields, f.word_ids, s_words);
    delete from app.fact_flags g where g.fact_id = f.id and g.flagged_for = 'recheck';
  end loop;
  perform set_config('app.fact_checks_reset', '', true);
  return null;
end
$$;

create trigger cases_named_expert_reset after update of named_expert_id on app.cases
  for each row when (old.named_expert_id is distinct from new.named_expert_id)
  execute function app.cases_named_expert_reset();

revoke execute on function app.word_box(uuid, text[]), app.cases_named_expert_reset()
  from public, app_api, app_worker;

reset role;
