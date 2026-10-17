-- 0017 foundation: what the bundle pipeline (slice A) needs from core tables (architecture 4.1, 5.3, 7).
--
-- * `job_kind_actions`: the virus scan of one document and the reprocessing of a whole case are
--   queued by whoever may upload to the case (`case.upload`).
-- * `uploads.append`: the upload is of later records added to a case that already has a bundle;
--   completing it publishes `DocumentUploaded(appended=true)` so the new pages continue the numbering.

set role app_migrator;

insert into app.job_kind_actions (kind, action) values
  ('scan_document', 'case.upload'),
  ('reprocess_case', 'case.upload');

alter table app.uploads add column append boolean not null default false;

reset role;
