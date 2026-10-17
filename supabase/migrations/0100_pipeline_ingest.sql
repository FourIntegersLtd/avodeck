-- 0100 pipeline: what slice A keeps about each uploaded file and each page (architecture 4.1, 7 steps 1-2).
--
-- * `document_scans`: the virus scan of one document (clean or the signature found) and when its
--   pages were cut. An infected document never becomes pages.
-- * `page_analysis`: the pipeline's own working notes on one page (stage reached, hashes for the
--   duplicate check, dates printed on it, whether it needs a person). Never page text.
-- * `ocr_words`: every word on a page, from the text layer or OCR, each word sealed with the case key.
--
-- The API sees rows with `case.view_content`; the worker only inside a job on the same case.
-- A page's rows must name the page's own case (checked by `app.pipeline_same_case_page`).

set role app_migrator;

create table app.document_scans (
  document_id uuid primary key references app.documents (id) on delete cascade,
  case_id uuid not null references app.cases (id) on delete cascade,
  clean boolean not null,
  signature text,
  scanned_at timestamptz not null default now(),
  split_at timestamptz,
  created_at timestamptz not null default now(),
  check (clean or signature is not null)
);
create index document_scans_case on app.document_scans (case_id);

create table app.page_analysis (
  page_id uuid primary key references app.pages (id) on delete cascade,
  case_id uuid not null references app.cases (id) on delete cascade,
  stage text not null default 'split',
  word_count integer,
  phash bigint,
  minhash bigint[],
  printed_dates date[] not null default '{}',
  needs_human boolean not null default false,
  reason text,
  model_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index page_analysis_case on app.page_analysis (case_id);

create table app.ocr_words (
  page_id uuid not null references app.pages (id) on delete cascade,
  case_id uuid not null references app.cases (id) on delete cascade,
  n integer not null check (n > 0),
  word_id text not null,
  text bytea not null,
  polygon real[] not null check (cardinality(polygon) = 8),
  confidence real not null check (confidence between 0 and 1),
  handwritten boolean not null default false,
  struck boolean not null default false,
  created_at timestamptz not null default now(),
  primary key (page_id, word_id),
  unique (page_id, n)
);
create index ocr_words_case on app.ocr_words (case_id);

create function app.pipeline_same_case_page() returns trigger
language plpgsql as $$
begin
  if not exists (select 1 from app.pages p where p.id = new.page_id and p.case_id = new.case_id) then
    raise exception 'page belongs to another case' using errcode = 'check_violation';
  end if;
  return new;
end
$$;

create trigger page_analysis_same_case before insert or update on app.page_analysis
  for each row execute function app.pipeline_same_case_page();
create trigger ocr_words_same_case before insert or update on app.ocr_words
  for each row execute function app.pipeline_same_case_page();

alter table app.document_scans enable row level security;
alter table app.page_analysis enable row level security;
alter table app.ocr_words enable row level security;
revoke update, delete on app.document_scans from app_api;
revoke insert, update, delete on app.page_analysis, app.ocr_words from app_api;

create policy document_scans_view on app.document_scans for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy document_scans_job on app.document_scans to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());
create policy page_analysis_view on app.page_analysis for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy page_analysis_job on app.page_analysis to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());
create policy ocr_words_view on app.ocr_words for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy ocr_words_job on app.ocr_words to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

reset role;
