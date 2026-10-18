-- 0606 report: invoice details approved on 2026-10-18 (screen 18b).
-- billing_profiles: optional bank details (account name, sort code, account number, IBAN, SWIFT/BIC), checked
--   loosely. Plain columns: billing profiles are not case data and are not sealed with the case key. An
--   organisation's owner or admin may now see and change the organisation's profile (bank details only, through the
--   API); the person's own profile stays theirs alone.
-- case_billing_profile: also returns the bank details, so the draft's issuer snapshot can carry them.
-- invoices.statements: the jurisdiction pack's invoice lines, frozen when the invoice is drafted.
-- invoices.estimate_amount: the time estimate in money, frozen when drafted (null without an estimate).
-- time_estimates: the expert's estimate per workflow step, kept as new rows (never changed or deleted); the
--   newest row per step is the estimate.

set role app_migrator;

alter table app.billing_profiles
  add column bank_account_name text check (length(bank_account_name) between 1 and 140),
  add column bank_sort_code text check (bank_sort_code ~ '^[0-9]{6}$'),
  add column bank_account_number text check (bank_account_number ~ '^[0-9]{4,20}$'),
  add column bank_iban text check (bank_iban ~ '^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$'),
  add column bank_bic text check (bank_bic ~ '^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$');

create policy billing_profiles_org_view on app.billing_profiles for select to app_api
  using (owner_type = 'organisation' and exists (
           select 1 from app.memberships m
            where m.organisation_id = owner_id and m.person_id = app.current_person_id()
              and m.ended_at is null and m.role in ('owner', 'admin')));
create policy billing_profiles_org_change on app.billing_profiles for update to app_api
  using (owner_type = 'organisation' and exists (
           select 1 from app.memberships m
            where m.organisation_id = owner_id and m.person_id = app.current_person_id()
              and m.ended_at is null and m.role in ('owner', 'admin')))
  with check (owner_type = 'organisation' and exists (
           select 1 from app.memberships m
            where m.organisation_id = owner_id and m.person_id = app.current_person_id()
              and m.ended_at is null and m.role in ('owner', 'admin')));

drop function app.case_billing_profile(uuid);
create function app.case_billing_profile(p_case uuid)
  returns table (id uuid, legal_name text, address text, tax_number text, tax_rate numeric, currency text,
                 hourly_rate numeric, invoice_prefix text, bank_account_name text, bank_sort_code text,
                 bank_account_number text, bank_iban text, bank_bic text)
  language sql
  stable
  security definer
  set search_path = ''
as $$
  select b.id, b.legal_name, b.address, b.tax_number, b.tax_rate, b.currency, b.hourly_rate, b.invoice_prefix,
         b.bank_account_name, b.bank_sort_code, b.bank_account_number, b.bank_iban, b.bank_bic
    from app.cases c
    join app.organisations o on o.id = c.organisation_id
    join app.billing_profiles b
      on (o.kind = 'personal' and b.owner_type = 'person' and b.owner_id = c.named_expert_id)
      or (o.kind = 'team' and b.owner_type = 'organisation' and b.owner_id = c.organisation_id)
   where c.id = p_case
     and (app.can_access_case(p_case, 'invoice.create') or app.can_access_case(p_case, 'invoice.send')
          or app.can_access_case(p_case, 'invoice.record_payment'))
$$;
revoke all on function app.case_billing_profile(uuid) from public;
grant execute on function app.case_billing_profile(uuid) to app_api;

alter table app.invoices
  add column statements jsonb not null default '[]' check (jsonb_typeof(statements) = 'array'),
  add column estimate_amount numeric(12, 2) check (estimate_amount >= 0);

create table app.time_estimates (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  stage text not null check (stage ~ '^[a-z][a-z0-9_]{0,39}$'),
  hours numeric(7, 2) not null check (hours >= 0),
  fee numeric(12, 2) check (fee >= 0),
  created_by uuid not null default app.current_person_id() references app.people (id),
  created_at timestamptz not null default clock_timestamp()
);
create index time_estimates_case on app.time_estimates (case_id, stage, created_at desc);
alter table app.time_estimates enable row level security;
create policy time_estimates_view on app.time_estimates for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy time_estimates_add on app.time_estimates for insert to app_api
  with check (app.can_access_case(case_id, 'invoice.create') and created_by = app.current_person_id());
create policy time_estimates_job on app.time_estimates for select to app_worker
  using (case_id = app.current_job_case_id());
revoke update, delete, truncate on app.time_estimates from app_api, app_worker;

reset role;
