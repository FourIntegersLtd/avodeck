set role app_migrator;

alter table app.pages alter column transcribed_by_expert_text type text using null;

comment on column app.pages.transcribed_by_expert_text is null;

reset role;
