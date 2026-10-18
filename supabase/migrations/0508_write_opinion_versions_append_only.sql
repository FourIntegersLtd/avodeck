-- 0508 write (slice E): a saved opinion version never changes, even for the owner role. Slice B's
-- `app.append_only_log()` (0202) refuses every update and delete (AV409), except deletes while the case
-- is being purged (`app.purging_case_id`, 0599 order). Grants already keep app_api to select (0502).

set role app_migrator;

create trigger opinion_versions_append_only before update or delete on app.opinion_versions
  for each row execute function app.append_only_log();

reset role;
