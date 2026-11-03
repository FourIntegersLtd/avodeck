-- 0131: the job that asks again about only the pages the model could not read (slice A).
-- Needs page.read, like the entries job: the reader presses "Try these pages again".

set role app_migrator;

insert into app.job_kind_actions (kind, action) values
  ('retry_unread_pages', 'page.read');

reset role;
