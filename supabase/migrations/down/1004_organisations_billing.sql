set role app_migrator;

drop policy billing_profiles_org_add on app.billing_profiles;

reset role;
