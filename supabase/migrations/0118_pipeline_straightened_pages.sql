-- 0118 pipeline: a page stored the right way up by the detected page angle (plan 2026-10-23, item 5).
--
-- When a scanned page is first read, the angle Document Intelligence detects is snapped to a quarter
-- turn and stored in the page's existing `rotation`; `straightened` records that we did it, so the
-- summary can count them and a later reading never turns the page again. No status changes, and the
-- page keeps its number.

set role app_migrator;

alter table app.pages add column straightened boolean not null default false;

reset role;
