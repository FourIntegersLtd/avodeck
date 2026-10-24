-- 1012 organisations: the support console's look-ups of a person, an organisation and the cell (support plan
-- phase 2; permissions.md "Avodeck staff").
--
-- Each answers current support staff only (`app.is_support_staff`, 0030) and null to anyone else, like
-- app.support_cases() (1006). Counts, flags, roles, states, timestamps and ids only: never a name, an email
-- (the person's, a new one being confirmed, a custodian's or an invitee's), an invite token, an
-- organisation's name or a date of birth (rule 6).
--
-- app.support_person(email, max_failures, window)  people whose sign-in email is `email` (any case): whether
--     MFA is set up (their latest session says; null if they never signed in), until when they are locked
--     out of the code step (`max_failures` wrong codes inside `window`, the API's own MFA_MAX_FAILURES and
--     MFA_WINDOW, so the rule lives in one place), unused backup codes, their last completed MFA, whether
--     an email change waits to be confirmed, whether a records custodian is named, and the cases that stop
--     the account closing (as account closure: their own practice's cases they are named on, never
--     served, not deleted or being deleted, and not exported by them).
-- app.support_organisation(org)  kind, dates, members (role and dates), invites (state, sent, expires; a
--     pending invite past its expiry reads as expired), owners now, open cases (not settled, withdrawn,
--     archived or deleted), and when its cases changed named expert (audit rows `case.named_expert_changed`).
-- app.support_cell_status()  how long ago the freshest worker heartbeat was (null: none), jobs waiting
--     and jobs paused. The queue's tables are the worker's (0007), so the API reads them only through here.

set role app_migrator;

create function app.support_person(p_email text, p_max_failures integer, p_window interval) returns jsonb
  language sql stable
  security definer
  set search_path = ''
as $$
  select case when app.is_support_staff(app.current_person_id()) then coalesce((
    select jsonb_agg(jsonb_build_object(
             'person_id', p.id,
             'created_at', p.created_at,
             'closed_at', p.closed_at,
             'mfa_enrolled', (select s.mfa_enrolled from app.sessions s where s.person_id = p.id
                               order by s.created_at desc limit 1),
             'mfa_locked_until', (select a.window_started_at + p_window from app.mfa_attempts a
                                   where a.person_id = p.id and a.failures >= p_max_failures
                                     and a.window_started_at > now() - p_window),
             'recovery_codes_left', (select count(*) from app.mfa_recovery_codes r
                                      where r.person_id = p.id and r.used_at is null),
             'last_mfa_at', (select max(s.mfa_verified_at) from app.sessions s where s.person_id = p.id),
             'pending_email_change', exists (
               select 1 from app.email_changes e where e.person_id = p.id and e.completed_at is null
                  and e.cancelled_at is null and e.expires_at > now()),
             'custodian_set', p.records_custodian_email is not null,
             'blocking_case_ids_for_closure', coalesce((
               select jsonb_agg(c.id order by c.created_at, c.id)
                 from app.cases c join app.organisations o on o.id = c.organisation_id
                where o.kind = 'personal' and c.named_expert_id = p.id and c.status <> 'deleted'
                  and c.retention_policy is distinct from 'delete_now' and not app.case_ever_served(c.id)
                  and not exists (select 1 from app.jobs j where j.case_id = c.id and j.kind = 'export_case'
                                     and j.status = 'done' and j.requested_by = p.id)), '[]'::jsonb))
           order by p.created_at, p.id)
      from app.people p where lower(p.email) = lower(p_email)), '[]'::jsonb) end
$$;

create function app.support_organisation(p_org uuid) returns jsonb
  language sql stable
  security definer
  set search_path = ''
as $$
  select jsonb_build_object(
           'organisation_id', o.id,
           'kind', o.kind,
           'created_at', o.created_at,
           'closed_at', o.closed_at,
           'owner_count', (select count(*) from app.memberships m
                            where m.organisation_id = o.id and m.role = 'owner' and m.ended_at is null),
           'open_case_count', (select count(*) from app.cases c where c.organisation_id = o.id
                                and c.status not in ('settled', 'withdrawn', 'archived', 'deleted')),
           'members', coalesce((
             select jsonb_agg(jsonb_build_object('person_id', m.person_id, 'role', m.role,
                                                 'started_at', m.started_at, 'ended_at', m.ended_at)
                              order by m.ended_at nulls first, m.started_at, m.id)
               from app.memberships m where m.organisation_id = o.id), '[]'::jsonb),
           'invites', coalesce((
             select jsonb_agg(jsonb_build_object(
                      'id', i.id, 'role', i.role,
                      'state', case when i.status = 'pending' and i.expires_at <= now() then 'expired' else i.status end,
                      'sent_at', i.created_at, 'expires_at', i.expires_at)
                    order by i.created_at desc, i.id)
               from app.invites i where i.organisation_id = o.id), '[]'::jsonb),
           'reassignments', coalesce((
             select jsonb_agg(jsonb_build_object('case_id', a.case_id, 'changed_at', a.at) order by a.at, a.seq)
               from app.audit_log a join app.cases c on c.id = a.case_id
              where c.organisation_id = o.id and a.action = 'case.named_expert_changed'), '[]'::jsonb))
    from app.organisations o
   where o.id = p_org and app.is_support_staff(app.current_person_id())
$$;

create function app.support_cell_status() returns jsonb
  language sql stable
  security definer
  set search_path = ''
as $$
  select case when app.is_support_staff(app.current_person_id()) then jsonb_build_object(
    'worker_heartbeat_age_seconds', (select floor(extract(epoch from now() - max(w.last_heartbeat)))::integer
                                       from procrastinate.procrastinate_workers w),
    'queue_depth', (select count(*) from app.jobs j where j.status = 'queued'),
    'paused_jobs', (select count(*) from app.jobs j where j.status = 'paused')) end
$$;

revoke execute on function app.support_person(text, integer, interval), app.support_organisation(uuid),
  app.support_cell_status() from public, app_worker;
grant execute on function app.support_person(text, integer, interval), app.support_organisation(uuid),
  app.support_cell_status() to app_api;

reset role;
