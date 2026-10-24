-- Splitting a two-page scan (ADR 0019): the halves are new pages added after the last one.
-- documents.split_of_page_id says a generated document is the halves of that page; once the document
-- has been cut into pages, pages.split_of_page_id points each half back at the page it came from.
alter table app.documents add column split_of_page_id uuid references app.pages (id);
alter table app.pages add column split_of_page_id uuid references app.pages (id);
create index pages_split_of on app.pages (split_of_page_id) where split_of_page_id is not null;
create unique index documents_split_once on app.documents (split_of_page_id) where split_of_page_id is not null;
