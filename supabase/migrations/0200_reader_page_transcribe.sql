-- 0200 reader [foundation path, approved for slice B]: `page.transcribe`, typing out a page we couldn't read,
-- a second reading, relevance and asking for a clearer copy (S10). Same people as `page.rotate`.
-- The matrix function is replaced whole (it is one table).

set role app_migrator;

create or replace function app.case_action_allowed(action text, case_role text, org_role text, org_kind text)
  returns boolean
  language sql immutable parallel safe
  set search_path = ''
as $$
  select coalesce(bool_or(
           case_role = any (r.case_roles)
           or (org_role in ('owner', 'admin') and case r.org_grant
                 when 'admin' then true
                 when 'owner' then org_role = 'owner'
                 when 'team_admin' then org_kind = 'team'
                 else false end)
         ), false)
  from (values
    ('case.view_metadata',        '{named_expert,co_expert,supervisor,assistant,viewer}'::text[], 'admin'),
    ('case.view_content',         '{named_expert,co_expert,supervisor,assistant,viewer}', 'none'),
    ('case.edit_setup',           '{named_expert,assistant}', 'none'),
    ('case.upload',               '{named_expert,assistant}', 'none'),
    ('case.manage_members',       '{named_expert}', 'admin'),
    ('case.reassign_named_expert','{}', 'admin'),
    ('page.read',                 '{named_expert,co_expert,supervisor,assistant}', 'none'),
    ('page.quarantine',           '{named_expert,assistant}', 'none'),
    ('page.replace',              '{named_expert,assistant}', 'none'),
    ('page.rotate',               '{named_expert,assistant}', 'none'),
    ('page.review_duplicate',     '{named_expert,assistant}', 'none'),
    ('page.transcribe',           '{named_expert,assistant}', 'none'),
    ('fact.verify',               '{named_expert}', 'none'),
    ('question.confirm',          '{named_expert}', 'none'),
    ('opinion.write',             '{named_expert}', 'none'),
    ('comment.write',             '{named_expert,co_expert,supervisor,assistant}', 'none'),
    ('report.build',              '{named_expert,assistant}', 'none'),
    ('report.sign',               '{named_expert}', 'none'),
    ('report.serve',              '{named_expert}', 'none'),
    ('report.withdraw',           '{named_expert}', 'none'),
    ('request.draft',             '{named_expert,assistant}', 'none'),
    ('request.send',              '{named_expert}', 'none'),
    ('invoice.create',            '{named_expert,assistant}', 'team_admin'),
    ('invoice.send',              '{named_expert}', 'team_admin'),
    ('credit_note.issue',         '{named_expert}', 'team_admin'),
    ('invoice.record_payment',    '{named_expert,assistant}', 'admin'),
    ('case.export',               '{named_expert}', 'admin'),
    ('case.retention',            '{named_expert}', 'admin'),
    ('legal_hold.set',            '{named_expert}', 'owner'),
    ('legal_hold.release',        '{named_expert}', 'owner'),
    ('case.withdraw',             '{named_expert}', 'none'),
    ('support_access.grant',      '{named_expert}', 'none'),
    ('case.delete_now',           '{named_expert}', 'owner'),
    ('case.settle',               '{named_expert}', 'none'),
    ('case.archive',              '{named_expert}', 'none'),
    ('case.reopen',               '{named_expert}', 'none'),
    ('case.view_audit',           '{named_expert}', 'admin'),
    ('case.view_certificate',     '{named_expert}', 'admin'),
    ('case.hold',                 '{named_expert,assistant}', 'none'),
    ('case.change_jurisdiction',  '{named_expert}', 'none'),
    ('conflict.declare',          '{named_expert}', 'none')
  ) as r (action, case_roles, org_grant)
  where r.action = case_action_allowed.action
$$;

reset role;
