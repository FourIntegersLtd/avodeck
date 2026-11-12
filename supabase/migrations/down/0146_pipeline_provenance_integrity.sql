-- 0146 down: drop the provenance-integrity columns, guards and the confirmed-only progress filter.

set role app_migrator;

drop trigger if exists bundles_high_water_guard on app.bundles;
drop function if exists app.bundles_high_water_guard();
drop trigger if exists pages_source_index_guard on app.pages;
drop function if exists app.pages_source_index_guard();
drop trigger if exists facts_withdrawn_guard on app.facts;
drop function if exists app.facts_withdrawn_guard();

alter table app.documents drop column if exists pages_declared;
update app.documents set unreadable_reason = 'damaged' where unreadable_reason = 'altered';
alter table app.documents
  drop constraint documents_unreadable_reason_check,
  add constraint documents_unreadable_reason_check check (unreadable_reason in ('locked', 'damaged'));
alter table app.bundles drop column if exists page_high_water;
alter table app.pages drop column if exists source_page_index, drop column if exists content_sha256;
alter table app.page_analysis drop column if exists read_failure, drop column if exists refusal_codes;
alter table app.facts drop column if exists withdrawn_reason;

-- The progress function keeps the confirmed-only filter until 0142 is reapplied by hand.

reset role;
