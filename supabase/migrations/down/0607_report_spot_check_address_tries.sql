set role app_migrator;
drop function if exists app.spot_check_address_try(text, integer, interval);
drop table if exists app.spot_check_address_tries;
reset role;
