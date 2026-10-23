set role app_migrator;

grant execute on function
  app.is_org_admin(uuid),
  app.create_team_organisation(text, text),
  app.organisation_members(uuid),
  app.memberships_actor(),
  app.invites_guard(),
  app.accept_invite(text),
  app.organisation_template_exists(uuid, uuid),
  app.organisations_policy_guard(),
  app.close_organisation(uuid),
  app.support_cases(uuid, text),
  app.support_case_grants(uuid)
to app_worker;

reset role;
