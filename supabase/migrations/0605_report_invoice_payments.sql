-- Slice F: invoice payments and the issuer snapshot (architecture 4.7, 10.8).
-- invoice_payments: one row per payment received, never changed or deleted. It has no case_id: it belongs
--   to its invoice, is seen and recorded through the invoice's case (rule 9) and goes when the invoice goes
--   (on delete cascade), so the case purge reaches it through invoices.
-- invoices.paid_amount is kept as the sum of the invoice's payments; it is never more than the total less
--   its credit notes, and a sent invoice's status follows the sums (part_paid, then paid).
-- invoices.tax_rate / issuer: the issuer's tax rate, name, address and tax number when the invoice was
--   drafted, so a sent invoice's PDF never picks up later changes to the billing profile.

set role app_migrator;

-- The issuer snapshot -----------------------------------------------------------------------------------------
alter table app.invoices
  add column tax_rate numeric(5, 2) check (tax_rate >= 0 and tax_rate <= 100),
  add column issuer jsonb check (jsonb_typeof(issuer) = 'object');

alter table app.invoices disable trigger invoices_guard;
update app.invoices i
   set tax_rate = b.tax_rate,
       issuer = jsonb_build_object('legal_name', b.legal_name, 'address', b.address, 'tax_number', b.tax_number)
  from app.billing_profiles b
 where b.id = i.billing_profile_id;
alter table app.invoices enable trigger invoices_guard;

alter table app.invoices alter column tax_rate set not null, alter column issuer set not null;

create function app.invoices_snapshot() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if new.tax_rate is null or new.issuer is null then
    select coalesce(new.tax_rate, b.tax_rate),
           coalesce(new.issuer, jsonb_build_object('legal_name', b.legal_name, 'address', b.address,
                                                   'tax_number', b.tax_number))
      into new.tax_rate, new.issuer
      from app.billing_profiles b where b.id = new.billing_profile_id;
  end if;
  return new;
end
$$;
revoke all on function app.invoices_snapshot() from public;
create trigger invoices_snapshot before insert on app.invoices
  for each row execute function app.invoices_snapshot();

-- Payments ----------------------------------------------------------------------------------------------------
create table app.invoice_payments (
  id uuid primary key default gen_random_uuid(),
  invoice_id uuid not null references app.invoices (id) on delete cascade,
  amount numeric(12, 2) not null check (amount > 0),
  recorded_by uuid default app.current_person_id(),
  recorded_at timestamptz not null default now()
);
create index invoice_payments_invoice on app.invoice_payments (invoice_id, recorded_at);

create function app.invoice_payments_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  inv record;
begin
  select i.status into inv from app.invoices i where i.id = new.invoice_id for update;
  if not found or inv.status not in ('sent', 'part_paid') then
    perform app.refuse('only a sent invoice with something owed takes a payment');
  end if;
  new.recorded_at := now();
  return new;
end
$$;
revoke all on function app.invoice_payments_guard() from public;
create trigger invoice_payments_guard before insert on app.invoice_payments
  for each row execute function app.invoice_payments_guard();

create function app.invoice_payments_apply() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  update app.invoices i
     set paid_amount = (select coalesce(sum(p.amount), 0) from app.invoice_payments p where p.invoice_id = i.id),
         version = i.version + 1
   where i.id = new.invoice_id;
  return null;
end
$$;
revoke all on function app.invoice_payments_apply() from public;
create trigger invoice_payments_apply after insert on app.invoice_payments
  for each row execute function app.invoice_payments_apply();

-- The balance: paid is the sum of payments, never more than owed; the status follows ----------------------
-- Fires between invoices_actor and invoices_guard (triggers run in name order).
create function app.invoices_balance() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  owed numeric;
  payments numeric;
begin
  if old.status = 'draft' then
    return new;
  end if;
  -- The application's roles move paid_amount only by adding a payment (security definer above).
  if session_user in ('app_api', 'app_worker') then
    select coalesce(sum(amount), 0) into payments from app.invoice_payments where invoice_id = new.id;
    if new.paid_amount <> payments then
      perform app.refuse('the paid amount is the sum of the payments; record a payment');
    end if;
  end if;
  owed := new.total - (select coalesce(sum(amount), 0) from app.credit_notes where invoice_id = new.id);
  if new.paid_amount > owed then
    perform app.refuse('payments cannot exceed the invoice total less its credit notes');
  end if;
  if new.status in ('sent', 'part_paid', 'paid') and new.paid_amount > 0 then
    new.status := case when new.paid_amount >= owed then 'paid' else 'part_paid' end;
  end if;
  return new;
end
$$;
revoke all on function app.invoices_balance() from public;
create trigger invoices_balance before update on app.invoices
  for each row execute function app.invoices_balance();

-- A credit note re-checks the balance (it cannot take what is owed below what was paid).
create function app.credit_notes_balance() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  update app.invoices set version = version + 1
   where id = new.invoice_id and status in ('sent', 'part_paid', 'paid');
  return null;
end
$$;
revoke all on function app.credit_notes_balance() from public;
create trigger credit_notes_balance after insert on app.credit_notes
  for each row execute function app.credit_notes_balance();

-- Access (rule 9) ---------------------------------------------------------------------------------------------
alter table app.invoice_payments enable row level security;
create policy invoice_payments_view on app.invoice_payments for select to app_api
  using (exists (select 1 from app.invoices i where i.id = invoice_id
                  and (app.can_access_case(i.case_id, 'invoice.create')
                       or app.can_access_case(i.case_id, 'invoice.send')
                       or app.can_access_case(i.case_id, 'invoice.record_payment'))));
create policy invoice_payments_add on app.invoice_payments for insert to app_api
  with check (exists (select 1 from app.invoices i where i.id = invoice_id
                       and app.can_access_case(i.case_id, 'invoice.record_payment'))
              and recorded_by = app.current_person_id());
create policy invoice_payments_job on app.invoice_payments for select to app_worker
  using (exists (select 1 from app.invoices i where i.id = invoice_id and i.case_id = app.current_job_case_id()));
revoke update, delete, truncate on app.invoice_payments from app_api, app_worker;
revoke insert on app.invoice_payments from app_worker;

reset role;
