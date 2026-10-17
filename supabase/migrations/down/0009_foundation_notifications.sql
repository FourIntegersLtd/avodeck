-- Down for 0009 foundation messages, message choices and the bounce flag.
drop function if exists app.notify(uuid, uuid, uuid, text), app.notifications_daily(date), app.mark_email_bounced(text);
drop trigger if exists people_email_changed on app.people;
drop function if exists app.people_email_changed();
grant update on app.notifications to app_api;
drop index if exists app.notifications_to_email;
alter table app.people drop column if exists email_bounced_at;
alter table app.notifications drop constraint if exists notifications_kind_code;
alter table app.notifications drop column if exists in_app;
alter table app.notification_preferences drop column if exists enabled;
