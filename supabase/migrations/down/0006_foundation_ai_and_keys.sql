-- Down for 0006 foundation AI call log, case keys and sealed session tokens.
alter table app.sessions drop constraint if exists sessions_auth_tokens_sealed;
drop function if exists app.destroy_case_key(uuid), app.ai_budget(uuid);
drop table if exists app.case_keys, app.ai_calls;
