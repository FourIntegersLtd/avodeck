set role app_migrator;

drop table app.missing_items;
drop table app.record_gaps;
drop function app.missing_items_guard();
drop function app.missing_item_status_allowed(text, text);
alter table app.page_analysis drop column references_checked_at;

reset role;
