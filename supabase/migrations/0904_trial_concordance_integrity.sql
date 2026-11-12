-- 0904 trial: the concordance keeps what it replaced, and two pages of one court bundle never share a trial page
-- (security audit 16, PV-16c and PV-16d).
--
-- * `concordance_history`: one row each time a page's trial number is set by the expert, holding what was there
--   before (number, method, score, whether it was confirmed) and who changed it and when. Append-only: the API
--   role may read and add rows, nothing may change or remove one. It is keyed by `page_id` (the page, as on the
--   concordance row) and has no case column of its own: a row belongs to its concordance row (deleted with it
--   when a case is purged) and is read and added under the same access rule as that row.
-- * A trial page belongs to one page of the expert's bundle (the matcher gives each trial page to one page only),
--   so `(court_bundle_id, trial_page_no)` is unique where there is a number. Rows already sharing a number (only
--   an expert's own edit could have made them) keep the number on the confirmed, best-scoring row; the others
--   become "missing" and each change is written to the history first.

set role app_migrator;

create table app.concordance_history (
  id uuid primary key default gen_random_uuid(),
  concordance_id uuid not null references app.concordance (id) on delete cascade,
  court_bundle_id uuid not null references app.court_bundles (id) on delete cascade,
  page_id uuid not null references app.pages (id) on delete cascade,
  page_no integer not null,
  previous_trial_page_no integer,
  previous_method text not null check (previous_method in ('text', 'sequence', 'expert', 'none')),
  previous_score real not null check (previous_score between 0 and 1),
  previous_confirmed boolean not null,
  previous_confirmed_at timestamptz,
  new_trial_page_no integer check (new_trial_page_no >= 1),
  changed_by uuid default app.current_person_id(),
  created_at timestamptz not null default now()
);
create index concordance_history_row on app.concordance_history (concordance_id, created_at);

-- Append-only for every login, the owner's included (grants alone do not bind it, and a security-definer function
-- runs as its owner). A row is never changed. It may be deleted only when what it belongs to is gone (the row it
-- changed, its court bundle or its page): that is how a case purge removes it.
create function app.concordance_history_append_only() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if tg_op = 'DELETE' and (
       not exists (select 1 from app.concordance k where k.id = old.concordance_id)
       or not exists (select 1 from app.court_bundles b where b.id = old.court_bundle_id)
       or not exists (select 1 from app.pages p where p.id = old.page_id)) then
    return old;
  end if;
  perform app.refuse('concordance_history is append-only');
  return null;
end
$$;
create trigger concordance_history_append_only before update or delete on app.concordance_history
  for each row execute function app.concordance_history_append_only();

alter table app.concordance_history enable row level security;

create policy concordance_history_view on app.concordance_history for select to app_api
  using (exists (
    select 1 from app.concordance k
    where k.id = concordance_id and app.can_access_case(k.case_id, 'case.view_content')
  ));
create policy concordance_history_add on app.concordance_history for insert to app_api
  with check (exists (
    select 1 from app.concordance k
    where k.id = concordance_id and app.can_access_case(k.case_id, 'fact.verify')
  ));

revoke all on app.concordance_history from app_api, app_worker;
grant select, insert on app.concordance_history to app_api;

-- rows already sharing a trial page: keep the number on one, record the others' loss
create temporary table concordance_dupes on commit drop as
  select id from (
    select k.id, row_number() over (
      partition by k.court_bundle_id, k.trial_page_no
      order by k.confirmed desc, k.score desc, k.updated_at, k.id
    ) as rank
    from app.concordance k where k.trial_page_no is not null
  ) ranked where rank > 1;

insert into app.concordance_history (concordance_id, court_bundle_id, page_id, page_no, previous_trial_page_no,
    previous_method, previous_score, previous_confirmed, previous_confirmed_at, new_trial_page_no, changed_by)
  select k.id, k.court_bundle_id, k.page_id, k.page_no, k.trial_page_no, k.method, k.score, k.confirmed, k.confirmed_at, null, null
  from app.concordance k join concordance_dupes d on d.id = k.id;

update app.concordance k set trial_page_no = null, method = 'none', confirmed = false, confirmed_by = null,
    confirmed_at = null
  from concordance_dupes d where d.id = k.id;

create unique index concordance_trial_page on app.concordance (court_bundle_id, trial_page_no)
  where trial_page_no is not null;

reset role;
