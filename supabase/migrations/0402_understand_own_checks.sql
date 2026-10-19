-- Slice D: an expert's own checks (screen 13 "Add your own check").
-- A row in check_config with `custom` set is a check the person wrote: a label (their words) and the
-- parameters of one rule type the engine already has. It is theirs alone and runs only on their cases.
set role app_migrator;

alter table app.check_config add column label text check (length(label) <= 80);
alter table app.check_config add column custom jsonb;
alter table app.check_config add constraint check_config_custom_has_label
  check (custom is null or (label is not null and length(trim(label)) > 0));
