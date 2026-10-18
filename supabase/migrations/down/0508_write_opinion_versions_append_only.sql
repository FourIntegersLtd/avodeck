set role app_migrator;

drop trigger opinion_versions_append_only on app.opinion_versions;

reset role;
