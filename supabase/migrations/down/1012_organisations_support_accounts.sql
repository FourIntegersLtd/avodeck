-- Down 1012: accounts support created. Drops the record of who created them; the people and their own
-- practices stay (they belong to the people now).
set role app_migrator;
drop function if exists app.support_resend_account(uuid);
drop function if exists app.support_accounts_list();
drop function if exists app.support_create_account(uuid, text, text, text);
drop function if exists app.support_account_precheck(text);
drop function if exists app.support_account_check(text);
drop table if exists app.support_accounts;
reset role;
