-- 0380 setup: slice C's case tables join the purge order (with slice A's, 0112-0113, since this is the last
-- definition by number) (app.case_purge_order(), migration 0017), in the
-- same order as `PURGE_ORDER` in api/app/core/contracts/case_tables.py: each child table before the
-- table it references. The parties guard lets the purge remove a served case's parties (the purge sets
-- app.purging_case_id for the case it is removing); every other delete after serving is still refused.

set role app_migrator;

create or replace function app.case_purge_order() returns text[]
  language sql immutable parallel safe
  set search_path = ''
as $$
  select array['job_steps', 'instruction_extractions', 'prefill_runs', 'prefill_suggestions', 'assumptions', 'case_instructions', 'jobs', 'facts', 'ocr_words', 'page_analysis', 'page_embeddings', 'page_quarantines', 'page_replacements', 'missing_items', 'record_gaps', 'document_parts', 'document_scans', 'exhibits', 'uploads', 'pages', 'questions', 'instruction_versions', 'documents', 'instructing_party_requests', 'case_processing', 'report_versions', 'transcripts', 'ai_calls', 'notifications', 'idempotency_keys', 'case_parties', 'case_instructing_parties', 'case_links', 'case_subject_keys', 'case_withdrawals', 'conflict_answer_history', 'conflict_answers', 'conflict_declarations', 'case_members', 'legal_holds', 'case_keys', 'cases']
$$;

create or replace function app.case_parties_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'DELETE' and current_setting('app.purging_case_id', true) is not distinct from old.case_id::text then
    return old;
  end if;
  if app.case_ever_served(coalesce(new.case_id, old.case_id)) then
    perform app.refuse('the parties are fixed once the case is served');
  end if;
  if tg_op = 'DELETE' then
    return old;
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;

reset role;
