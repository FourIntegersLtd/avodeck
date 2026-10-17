-- Local development only (supabase db reset). Never run against staging or production.
-- Lets the restricted roles log in with local-only passwords; cells set real secrets out of band.
alter role app_api with login password 'app_api_local';
alter role app_worker with login password 'app_worker_local';
alter role app_migrator with login password 'app_migrator_local';
