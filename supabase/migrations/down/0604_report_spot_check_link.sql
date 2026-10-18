set role app_migrator;
drop function if exists app.spot_check_respond(text, text, jsonb);
drop function if exists app.spot_check_unlock(text, text);
reset role;
