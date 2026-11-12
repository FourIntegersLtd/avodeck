set role app_migrator;

drop function app.close_export_hash(uuid, uuid, text);

reset role;
