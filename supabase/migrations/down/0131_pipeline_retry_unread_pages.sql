-- 0131 down: the retry job kind.

set role app_migrator;

delete from app.job_kind_actions where kind = 'retry_unread_pages';

reset role;
