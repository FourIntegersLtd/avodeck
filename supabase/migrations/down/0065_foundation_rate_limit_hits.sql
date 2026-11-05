-- Undo 0065: no per-person request counts; the API's limiter has nothing to count with.
set role app_migrator;

drop function if exists app.rate_limit_hit(text);
drop table if exists app.rate_limit_hits;

reset role;
