-- 1004 organisations: a team's billing profile (architecture 4.2, 10.8; scenario S1).
--
-- billing_profiles (slice F's table, migration 0600) keeps personal profiles for the person themselves
-- (F's policies). Slice O adds: a current owner or admin of a team organisation may add its profile
-- (`organisation`, the organisation's id). Changing it is already allowed by 0606
-- (`billing_profiles_org_change`). Invoice numbers still move only by issuing (0600's trigger), per
-- profile, so each issuer's numbering is its own and has no gaps.

set role app_migrator;

create policy billing_profiles_org_add on app.billing_profiles for insert to app_api
  with check (owner_type = 'organisation' and app.is_org_admin(owner_id)
              and exists (select 1 from app.organisations o where o.id = owner_id and o.kind = 'team'));

reset role;
