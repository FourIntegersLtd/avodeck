set role app_migrator;

drop function app.accept_invite(text);
drop table app.invites;
drop function app.invites_guard();

reset role;
