-- 9006 foundation: which kinds are interactive, and the per-case cap of the ones a person queues in bulk (BG-09, BG-11).
--
-- Last in the foundation range so it sees every slice's rows in `app.job_kind_actions` (0070 added the columns).
-- A slice that adds a kind adds `(kind, action, interactive)` itself and, if a person may queue many at once,
-- `max_active`; the core test `test_every_registered_job_kind_has_a_row_whose_interactive_flag_is_the_registrys`
-- keeps the table and `core/jobs.py` in step. Kinds that had no row (they need `case.upload`, the default) get one.

set role app_migrator;

insert into app.job_kind_actions (kind, action) values
  ('extract_written_questions', 'case.upload'),
  ('finalize_upload', 'case.upload'),
  ('find_may_matter', 'case.upload'),
  ('prefill_case', 'case.upload'),
  ('read_agenda', 'case.upload'),
  ('reseal_trial_files', 'case.upload')
on conflict (kind) do nothing;

update app.job_kind_actions set interactive = kind in (
  'build_report', 'build_supplementary', 'build_trial_pack', 'compare_witness_statement', 'export_case',
  'extract_questions', 'extract_written_questions', 'final_check', 'finalize_upload', 'find_dependents',
  'match_trial_bundle', 'prefill_case', 'read_agenda', 'rerun_checks', 'suggest_points'
) where interactive is null and kind in (
  'build_report', 'build_supplementary', 'build_trial_pack', 'compare_witness_statement', 'delete_case',
  'export_case', 'export_transcript', 'export_transcript_pdf', 'extract_entries', 'extract_questions',
  'extract_written_questions', 'final_check', 'finalize_upload', 'find_dependents', 'find_may_matter',
  'match_trial_bundle', 'prefill_case', 'process_bundle', 'process_pages', 'read_agenda', 'read_page_batch',
  'repair_fact_dates', 'reprocess_case', 'reseal_trial_files', 'rerun_checks', 'retry_unread_pages',
  'scan_document', 'second_reading', 'suggest_links', 'suggest_points', 'verify_opposing'
);

-- A person queues one `finalize_upload` per uploaded file and one `second_reading` per page: a higher cap for them.
update app.job_kind_actions set max_active = 500 where kind = 'finalize_upload';
update app.job_kind_actions set max_active = 200 where kind in ('second_reading', 'scan_document');

reset role;
