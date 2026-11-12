-- 0147 down: a file marked too_many_pages goes back to damaged (it still has no pages), and the list shrinks.
-- `altered` is kept: it belongs to 0146, whose own down file removes it.

set role app_migrator;

update app.documents set unreadable_reason = 'damaged' where unreadable_reason = 'too_many_pages';
alter table app.documents
  drop constraint documents_unreadable_reason_check,
  add constraint documents_unreadable_reason_check check (unreadable_reason in ('locked', 'damaged', 'altered'));

reset role;
