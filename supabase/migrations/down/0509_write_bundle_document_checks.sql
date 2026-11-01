-- Undo 0509: restore witness_claims_guard and witness_comparisons_guard to their original
-- (0501/0506) bodies, which checked documents.case_id directly.
set role app_migrator;

create or replace function app.witness_claims_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  marking_stale boolean := pg_trigger_depth() > 1;  -- only the fact-change trigger (0504) updates from inside a trigger
begin
  if tg_op = 'INSERT' then
    if new.status <> 'suggested' then
      perform app.refuse('a new claim starts suggested');
    end if;
    if not exists (select 1 from app.documents d where d.id = new.document_id and d.case_id = new.case_id) then
      perform app.refuse('a claim comes from a document of its own case');
    end if;
    new.version := 1;
    return new;
  end if;
  if new.case_id is distinct from old.case_id or new.document_id is distinct from old.document_id
     or new.claim_index is distinct from old.claim_index or new.text_verbatim is distinct from old.text_verbatim
     or new.word_ids is distinct from old.word_ids or new.verdict is distinct from old.verdict
     or new.fact_ids is distinct from old.fact_ids then
    perform app.refuse('a claim keeps the statement''s exact words and what it was compared with');
  end if;
  if new.status = 'stale' and old.status <> 'stale' and not marking_stale then
    perform app.refuse('only a change to a fact makes a claim stale');
  end if;
  if new.status is distinct from old.status and new.status <> 'stale'
     and not (old.status in ('suggested', 'stale') and new.status in ('confirmed', 'edited', 'dismissed')) then
    perform app.refuse(format('claim status cannot change from %s to %s', old.status, new.status));
  end if;
  if not marking_stale and new.status is distinct from old.status then
    new.fact_versions := coalesce(
      (select array_agg(coalesce(f.version, 0) order by i.n)
       from unnest(new.fact_ids) with ordinality as i (id, n)
       left join app.facts f on f.id = i.id),
      '{}');
    new.decided_by := app.current_person_id();
    new.decided_at := now();
  elsif new.fact_versions is distinct from old.fact_versions then
    perform app.refuse('a claim records the fact versions it was compared with');
  end if;
  if new.status <> 'edited' then
    new.expert_verdict := null;
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;

create or replace function app.witness_comparisons_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if not exists (select 1 from app.documents d where d.id = new.document_id and d.case_id = new.case_id) then
      perform app.refuse('a comparison is of a document of its own case');
    end if;
    if new.status <> 'queued' then
      perform app.refuse('a comparison starts queued');
    end if;
    new.requested_by := app.current_person_id();
    new.claims := null;
    new.finished_at := null;
    return new;
  end if;
  if (to_jsonb(new) - array['status', 'claims', 'finished_at'])
     is distinct from (to_jsonb(old) - array['status', 'claims', 'finished_at']) then
    perform app.refuse('a comparison keeps its document, job and who asked');
  end if;
  if new.status is distinct from old.status and not (old.status = 'queued' and new.status in ('done', 'refused')) then
    perform app.refuse(format('comparison status cannot change from %s to %s', old.status, new.status));
  end if;
  if new.status is distinct from old.status then
    new.finished_at := now();
  end if;
  return new;
end
$$;

reset role;
