-- 0106 down: the instructing-party request log goes.

set role app_migrator;

drop table app.instructing_party_requests;
drop function app.instructing_party_requests_chain();

reset role;
