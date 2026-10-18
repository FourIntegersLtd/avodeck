-- Slice F: where an invoice comes from, and what slice H exports.
-- case_billing_profile: the issuer of a case's invoices (architecture 10.8): the organisation's profile for a
--   team case, the named expert's own for a personal case. Security definer so a case helper (who cannot
--   see the expert's personal profile row) can draft; it checks the invoice actions itself (rule 9).
-- case_invoice_rows: sent invoices of a case with what was credited, for `invoices.csv` in slice H's export.
--   Security invoker: row-level security on invoices and credit notes still applies (the case's own job,
--   or a person allowed to see invoices).

set role app_migrator;

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

create function app.case_invoice_rows(p_case uuid)
  returns table (invoice_id uuid, number integer, number_text text, status text, total numeric, tax numeric,
                 currency text, paid_amount numeric, credited numeric, sent_at timestamptz)
  language sql
  stable
  security invoker
  set search_path = ''
as $$
  select i.id, i.number, i.number_text, i.status, i.total, i.tax, i.currency, i.paid_amount,
         coalesce((select sum(n.amount) from app.credit_notes n where n.invoice_id = i.id), 0)::numeric(12, 2),
         i.sent_at
    from app.invoices i
   where i.case_id = p_case and i.status <> 'draft'
   order by i.sent_at, i.number
$$;
revoke all on function app.case_invoice_rows(uuid) from public;
grant execute on function app.case_invoice_rows(uuid) to app_api, app_worker;

reset role;
