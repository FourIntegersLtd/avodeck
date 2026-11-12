-- Down for 0904 trial: drop the trial-page uniqueness and the history (rows set to "missing" by the up
-- migration stay missing).
set role app_migrator;

drop index if exists app.concordance_trial_page;
drop table if exists app.concordance_history;
drop function if exists app.concordance_history_append_only();

reset role;
