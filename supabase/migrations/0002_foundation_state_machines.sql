-- 0002 foundation: docs/spec/state-machines.md enforced in the database.
--
-- Any status change not drawn in state-machines.md raises SQLSTATE 'AV409'; the API turns it
-- into 409 `conflict`. core/state_machines.py holds the same edges and is tested against the
-- document, so the service refuses first and this trigger catches anything that slips past.

set role app_migrator;

create function app.refuse(message text) returns void
  language plpgsql
  set search_path = ''
as $$ begin raise exception using errcode = 'AV409', message = message; end $$;

-- Case (`cases.status`) -------------------------------------------------------------------------
create function app.case_status_allowed(from_status text, to_status text) returns boolean
  language sql immutable parallel safe
  set search_path = ''
as $$
  select (from_status, to_status) in (
    ('draft', 'processing'),
    ('processing', 'active'),
    ('active', 'served'),
    ('served', 'settled'),
    ('active', 'settled'),
    ('settled', 'archived'),
    ('archived', 'active'),
    ('settled', 'active'),
    ('draft', 'withdrawn'),
    ('processing', 'withdrawn'),
    ('active', 'withdrawn'),
    ('served', 'withdrawn'),
    ('withdrawn', 'archived')
  ) or (to_status = 'deleted' and from_status <> 'deleted')
$$;

create function app.cases_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.status <> 'draft' then
      perform app.refuse('a new case starts as a draft');
    end if;
    new.version := 1;
    return new;
  end if;
  if new.status is distinct from old.status and not app.case_status_allowed(old.status, new.status) then
    perform app.refuse(format('case status cannot change from %s to %s', old.status, new.status));
  end if;
  -- Every change makes a new version, so a stale If-Match (412) always shows.
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;

create trigger cases_guard before insert or update on app.cases
  for each row execute function app.cases_guard();

-- Job (`jobs.status`) ---------------------------------------------------------------------------
create function app.jobs_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.status <> 'queued' then
      perform app.refuse('a new job starts queued');
    end if;
    return new;
  end if;
  if new.status is distinct from old.status and (old.status, new.status) not in (
    ('queued', 'running'), ('running', 'done'), ('running', 'failed'), ('failed', 'queued'),
    ('running', 'paused'), ('paused', 'running')
  ) then
    perform app.refuse(format('job status cannot change from %s to %s', old.status, new.status));
  end if;
  new.updated_at := now();
  return new;
end
$$;

create trigger jobs_guard before insert or update on app.jobs
  for each row execute function app.jobs_guard();

-- Membership (`memberships`) --------------------------------------------------------------------
-- active -> ended, terminal; an organisation keeps at least one owner; a personal organisation
-- membership never ends while the account is open. Leaving ends case-member access to that
-- organisation's cases (named experts keep theirs: they are not case members).
create function app.memberships_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  org_kind text;
begin
  if old.ended_at is not null then
    if new.ended_at is distinct from old.ended_at or new.role <> old.role then
      perform app.refuse('an ended membership cannot change');
    end if;
    return new;
  end if;
  if new.organisation_id <> old.organisation_id or new.person_id <> old.person_id then
    perform app.refuse('a membership cannot move');
  end if;
  -- Serialise changes per organisation so two owners cannot leave at once.
  select kind into org_kind from app.organisations where id = old.organisation_id for update;
  if new.ended_at is not null and org_kind = 'personal'
     and exists (select 1 from app.people p where p.id = old.person_id and p.closed_at is null) then
    perform app.refuse('a personal organisation membership lasts while the account is open');
  end if;
  if old.role = 'owner' and (new.ended_at is not null or new.role <> 'owner')
     and not exists (
       select 1 from app.memberships m
       where m.organisation_id = old.organisation_id and m.role = 'owner' and m.ended_at is null
         and m.id <> old.id) then
    perform app.refuse('an organisation always keeps at least one owner');
  end if;
  if new.ended_at is not null then
    update app.case_members cm set removed_at = new.ended_at
    from app.cases c
    where c.id = cm.case_id and c.organisation_id = old.organisation_id
      and cm.person_id = old.person_id and cm.removed_at is null;
  end if;
  return new;
end
$$;

create trigger memberships_guard before update on app.memberships
  for each row execute function app.memberships_guard();

-- Page flags (`pages`) ----------------------------------------------------------------------------
create function app.pages_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if new.bundle_page_no <> old.bundle_page_no then
    perform app.refuse('a page number never changes');
  end if;
  if old.superseded_by_page_id is not null and new.superseded_by_page_id is distinct from old.superseded_by_page_id then
    perform app.refuse('a superseded page stays superseded');
  end if;
  if old.unreadable and not new.unreadable and new.transcribed_by_expert_text is null then
    perform app.refuse('an unreadable page becomes readable only with the expert''s transcription');
  end if;
  return new;
end
$$;

create trigger pages_guard before update on app.pages
  for each row execute function app.pages_guard();

reset role;
