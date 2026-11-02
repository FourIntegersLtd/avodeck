-- 9000 foundation: run the SELECT-policy rewrite of 0052 once more, after every slice migration.
--
-- 0052 rewrites each `app.can_access_case(...)` / `app.can_access_bundle(...)` SELECT policy for app_api to
-- the once-per-query form (app.accessible_case_ids / app.accessible_bundle_ids). A fresh database runs
-- migrations in order, so policies that slices create in 0100-1099 come after 0052 and keep the per-row form.
-- This last migration (reserved range 9000-9099, foundation only) rewrites them. It is safe to run again.
--
-- A slice that adds a SELECT policy in the per-row form needs nothing more: this migration still runs last on
-- a fresh database. A slice that adds one LATER than a deployed 9000 (a new migration on an existing cell)
-- must end its migration with `select app.use_accessible_id_sets();`, or
-- test_no_select_policy_checks_access_one_row_at_a_time fails.

set role app_migrator;

select app.use_accessible_id_sets();

reset role;
