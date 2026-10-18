set role app_migrator;
drop table if exists app.time_estimates;
alter table app.invoices drop column if exists estimate_amount, drop column if exists statements;
drop function if exists app.case_billing_profile(uuid);
create function app.case_billing_profile(p_case uuid)
  returns table (id uuid, legal_name text, address text, tax_number text, tax_rate numeric, currency text,
                 hourly_rate numeric, invoice_prefix text)
  language sql
  stable
  security definer
  set search_path = ''
as $$
  select b.id, b.legal_name, b.address, b.tax_number, b.tax_rate, b.currency, b.hourly_rate, b.invoice_prefix
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
drop policy if exists billing_profiles_org_change on app.billing_profiles;
drop policy if exists billing_profiles_org_view on app.billing_profiles;
alter table app.billing_profiles drop column if exists bank_bic, drop column if exists bank_iban,
  drop column if exists bank_account_number, drop column if exists bank_sort_code,
  drop column if exists bank_account_name;
reset role;
