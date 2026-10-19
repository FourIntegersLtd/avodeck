-- Down for 0900 trial: drop slice I's tables, triggers and webhook functions.
set role app_migrator;

drop function if exists app.trial_esign_signed(text, text, bytea);
drop function if exists app.trial_esign_declined(text);
drop table if exists app.offline_packages, app.trial_packs, app.trial_pack_settings, app.concordance,
  app.court_bundles, app.agenda_items, app.agenda_versions, app.expert_meeting_participants, app.expert_meetings;
drop function if exists app.trial_meeting_guard();
drop function if exists app.trial_agenda_guard();
drop function if exists app.trial_bundle_guard();
drop function if exists app.trial_concordance_touch();
drop function if exists app.trial_settings_touch();
drop function if exists app.trial_offline_guard();

reset role;
