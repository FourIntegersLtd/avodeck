-- 0600 report (slice F): the built report, what was frozen at signing, who received it, final check
-- results, colleague spot-checks, time and invoices (architecture 4.7, 4.8).
--
-- report_paragraphs / citations: written only by the build_report job, onto a draft version; never
--   changed or deleted (a rebuild makes a new draft). Paragraph text and quoted values are encrypted
--   under the case key (they hold the expert's words and record words).
-- final_check_runs: written only by the final_check job; results hold check keys, codes and ids only.
-- report_snapshots: frozen at signing (before the version locks); never changed.
-- report_servings: one row per recipient of a served version; a mistake is corrected by a new row
--   (`correction_of` + `reason`), never an edit.
-- report_settings: the case's citation style and the pack correction the expert acknowledged (F34).
-- spot_checks: a colleague's check through an expiring link plus an emailed code (hashes only).
-- time_entries: time on the case by stage; people record their own time; adjustments are rows marked
--   `adjusted_by_expert`.
-- billing_profiles: who an invoice is from (personal here; team profiles are slice O's to write);
--   holds the next invoice and credit note numbers.
-- invoices / credit_notes: numbered from the issuer's profile when sent or issued, so numbers never
--   skip or repeat (the allocation rolls back with the transaction); a sent invoice changes only along
--   its payment states; corrections are credit notes.

set role app_migrator;

-- Report settings -------------------------------------------------------------------------------------------
create table app.report_settings (
  case_id uuid primary key references app.cases (id) on delete cascade,
  citation_style text not null default 'page' check (citation_style in ('page', 'paragraph')),
  acknowledged_declaration_version text check (length(acknowledged_declaration_version) <= 60),
  version integer not null default 1,
  updated_at timestamptz not null default now()
);
alter table app.report_settings enable row level security;
create policy report_settings_view on app.report_settings for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy report_settings_add on app.report_settings for insert to app_api
  with check (app.can_access_case(case_id, 'report.build'));
create policy report_settings_change on app.report_settings for update to app_api
  using (app.can_access_case(case_id, 'report.build')) with check (app.can_access_case(case_id, 'report.build'));
create policy report_settings_job on app.report_settings for select to app_worker
  using (case_id = app.current_job_case_id());
revoke delete on app.report_settings from app_api, app_worker;

-- Paragraphs and citations ----------------------------------------------------------------------------------
create table app.report_paragraphs (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  report_version_id uuid not null references app.report_versions (id) on delete cascade,
  para_no integer not null check (para_no > 0),
  section text not null check (section ~ '^[a-z][a-z0-9_]{0,39}$'),
  content_control_id text not null check (length(content_control_id) between 1 and 80),
  text bytea not null,
  question_id uuid references app.questions (id),
  created_at timestamptz not null default now(),
  unique (report_version_id, para_no)
);
create index report_paragraphs_case on app.report_paragraphs (case_id);

create table app.citations (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  report_version_id uuid not null references app.report_versions (id) on delete cascade,
  paragraph_id uuid not null references app.report_paragraphs (id) on delete cascade,
  cite_no integer not null check (cite_no > 0),
  fact_id uuid not null references app.facts (id),
  fact_version integer not null check (fact_version > 0),
  value_snapshot bytea not null,
  quote_verbatim bytea not null,
  page_id uuid not null references app.pages (id),
  created_at timestamptz not null default now()
);
create index citations_version on app.citations (report_version_id);
create index citations_case on app.citations (case_id);

create function app.report_rows_on_draft() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if not exists (select 1 from app.report_versions r
                 where r.id = new.report_version_id and r.case_id = new.case_id and r.locked_at is null) then
    perform app.refuse('built rows belong to a draft version of their own case');
  end if;
  return new;
end
$$;
revoke all on function app.report_rows_on_draft() from public;
create trigger report_paragraphs_on_draft before insert on app.report_paragraphs
  for each row execute function app.report_rows_on_draft();
create trigger citations_on_draft before insert on app.citations
  for each row execute function app.report_rows_on_draft();

-- Final check runs ------------------------------------------------------------------------------------------
create table app.final_check_runs (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  report_version_id uuid not null references app.report_versions (id) on delete cascade,
  results jsonb not null check (jsonb_typeof(results) = 'array'),
  warnings jsonb not null default '[]' check (jsonb_typeof(warnings) = 'array'),
  passed boolean not null,
  pack_versions jsonb not null default '{}',
  job_id uuid,
  at timestamptz not null default now(),
  created_at timestamptz not null default now()
);
create index final_check_runs_version on app.final_check_runs (report_version_id, at desc);
create index final_check_runs_case on app.final_check_runs (case_id, at desc);

-- Snapshots -------------------------------------------------------------------------------------------------
create table app.report_snapshots (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  report_version_id uuid not null references app.report_versions (id) on delete cascade,
  kind text not null check (kind ~ '^[a-z][a-z0-9_]{0,39}$'),
  content_ref text check (length(content_ref) <= 400),
  content bytea,
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  unique (report_version_id, kind)
);
create index report_snapshots_case on app.report_snapshots (case_id);
create trigger report_snapshots_on_draft before insert on app.report_snapshots
  for each row execute function app.report_rows_on_draft();

-- Servings --------------------------------------------------------------------------------------------------
create table app.report_servings (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  report_version_id uuid not null references app.report_versions (id) on delete cascade,
  recipient_party_id uuid references app.case_parties (id),
  served_on date not null,
  sent_via text not null check (sent_via ~ '^[a-z][a-z0-9_]{0,39}$'),
  correction_of uuid references app.report_servings (id),
  reason text check (reason ~ '^[a-z][a-z0-9_]{0,39}$'),
  created_by uuid default app.current_person_id(),
  created_at timestamptz not null default now(),
  constraint report_servings_correction_reason check ((correction_of is null) = (reason is null))
);
create index report_servings_version on app.report_servings (report_version_id);
create index report_servings_case on app.report_servings (case_id);

create function app.report_servings_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if not exists (select 1 from app.report_versions r
                 where r.id = new.report_version_id and r.case_id = new.case_id and r.status = 'served') then
    perform app.refuse('only a served report version of the case has servings');
  end if;
  if new.correction_of is not null and not exists (
       select 1 from app.report_servings s
       where s.id = new.correction_of and s.report_version_id = new.report_version_id) then
    perform app.refuse('a correction corrects a serving of the same version');
  end if;
  if new.recipient_party_id is not null and not exists (
       select 1 from app.case_parties p where p.id = new.recipient_party_id and p.case_id = new.case_id) then
    perform app.refuse('the recipient is a party to the case');
  end if;
  return new;
end
$$;
revoke all on function app.report_servings_guard() from public;
create trigger report_servings_guard before insert on app.report_servings
  for each row execute function app.report_servings_guard();

-- Spot checks -----------------------------------------------------------------------------------------------
create table app.spot_checks (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  report_version_id uuid not null references app.report_versions (id) on delete cascade,
  reviewer_email text not null check (reviewer_email ~ '^[^@\s]+@[^@\s]+$' and length(reviewer_email) <= 254),
  fact_ids uuid[] not null check (cardinality(fact_ids) between 1 and 50),
  token_hash text not null unique check (token_hash ~ '^[0-9a-f]{64}$'),
  code_hash text check (code_hash ~ '^[0-9a-f]{64}$'),
  code_sent_at timestamptz,
  code_attempts integer not null default 0 check (code_attempts >= 0),
  verified_at timestamptz,
  expires_at timestamptz not null,
  responses jsonb check (responses is null or jsonb_typeof(responses) = 'array'),
  responded_at timestamptz,
  created_by uuid not null default app.current_person_id(),
  created_at timestamptz not null default now()
);
create index spot_checks_case on app.spot_checks (case_id, created_at desc);

-- Time ------------------------------------------------------------------------------------------------------
create table app.time_entries (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  person_id uuid not null references app.people (id),
  stage text not null check (stage ~ '^[a-z][a-z0-9_]{0,39}$'),
  started_at timestamptz not null,
  ended_at timestamptz not null,
  seconds integer not null,
  adjusted_by_expert boolean not null default false,
  reason text check (reason ~ '^[a-z][a-z0-9_]{0,39}$'),
  created_at timestamptz not null default now(),
  check (ended_at >= started_at),
  check (adjusted_by_expert or seconds >= 0)
);
create index time_entries_case on app.time_entries (case_id, person_id, ended_at desc);

-- Billing profiles ------------------------------------------------------------------------------------------
create table app.billing_profiles (
  id uuid primary key default gen_random_uuid(),
  owner_type text not null check (owner_type in ('person', 'organisation')),
  owner_id uuid not null,
  legal_name text not null check (length(legal_name) between 1 and 200),
  address text check (length(address) <= 1000),
  tax_number text check (length(tax_number) <= 60),
  tax_rate numeric(5, 2) not null default 0 check (tax_rate >= 0 and tax_rate <= 100),
  currency text not null check (currency ~ '^[A-Z]{3}$'),
  hourly_rate numeric(12, 2) check (hourly_rate >= 0),
  invoice_prefix text not null default '' check (length(invoice_prefix) <= 20),
  next_invoice_no integer not null default 1 check (next_invoice_no > 0),
  next_credit_note_no integer not null default 1 check (next_credit_note_no > 0),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_type, owner_id)
);
alter table app.billing_profiles enable row level security;
create policy billing_profiles_view on app.billing_profiles for select to app_api
  using ((owner_type = 'person' and owner_id = app.current_person_id())
         or (owner_type = 'organisation' and owner_id = any (app.current_org_ids())));
create policy billing_profiles_add on app.billing_profiles for insert to app_api
  with check (owner_type = 'person' and owner_id = app.current_person_id());
create policy billing_profiles_change on app.billing_profiles for update to app_api
  using (owner_type = 'person' and owner_id = app.current_person_id())
  with check (owner_type = 'person' and owner_id = app.current_person_id());
revoke delete on app.billing_profiles from app_api, app_worker;

-- Numbers only move by issuing; nobody sets them by hand.
create function app.billing_profiles_numbers() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if current_user = 'app_api' then
    if tg_op = 'INSERT' then
      new.next_invoice_no := 1;
      new.next_credit_note_no := 1;
    else
      new.next_invoice_no := old.next_invoice_no;
      new.next_credit_note_no := old.next_credit_note_no;
      new.owner_type := old.owner_type;
      new.owner_id := old.owner_id;
    end if;
  end if;
  return new;
end
$$;
create trigger billing_profiles_numbers before insert or update on app.billing_profiles
  for each row execute function app.billing_profiles_numbers();

-- Invoices --------------------------------------------------------------------------------------------------
create table app.invoices (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  billing_profile_id uuid not null references app.billing_profiles (id),
  number integer check (number > 0),
  number_text text check (length(number_text) <= 40),
  lines jsonb not null check (jsonb_typeof(lines) = 'array'),
  total numeric(12, 2) not null check (total >= 0),
  tax numeric(12, 2) not null default 0 check (tax >= 0),
  currency text not null check (currency ~ '^[A-Z]{3}$'),
  status text not null default 'draft'
    check (status in ('draft', 'sent', 'part_paid', 'paid', 'written_off')),
  paid_amount numeric(12, 2) not null default 0 check (paid_amount >= 0),
  pdf_path text,
  sent_to text check (sent_to ~ '^[^@\s]+@[^@\s]+$' and length(sent_to) <= 254),
  sent_at timestamptz,
  created_by uuid default app.current_person_id(),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (billing_profile_id, number)
);
create index invoices_case on app.invoices (case_id, created_at);

create function app.invoices_actor() returns trigger
  language plpgsql
  set search_path = ''
as $$
declare
  needed text;
begin
  if current_user <> 'app_api' then
    return new;
  end if;
  needed := case
    when new.status is not distinct from old.status and old.status = 'draft' then 'invoice.create'
    when new.status = 'sent' then 'invoice.send'
    when new.status in ('part_paid', 'paid') then 'invoice.record_payment'
    when new.status = 'written_off' then 'credit_note.issue'
    else 'invoice.send' end;
  if not app.can_access_case(old.case_id, needed) then
    raise exception using errcode = '42501', message = 'this change needs ' || needed;
  end if;
  return new;
end
$$;
create trigger invoices_actor before update on app.invoices
  for each row execute function app.invoices_actor();

create function app.invoices_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  moving constant text[] := array['status', 'paid_amount', 'pdf_path', 'updated_at', 'version'];
  prefix text;
begin
  if tg_op = 'INSERT' then
    if new.status <> 'draft' or new.number is not null or new.sent_at is not null then
      perform app.refuse('a new invoice starts as an unnumbered draft');
    end if;
    return new;
  end if;
  new.updated_at := now();
  if old.status = 'draft' then
    if new.number is distinct from old.number or new.number_text is distinct from old.number_text
       or new.sent_at is distinct from old.sent_at then
      perform app.refuse('an invoice is numbered when it is sent');
    end if;
    if new.status = 'sent' then
      update app.billing_profiles set next_invoice_no = next_invoice_no + 1
        where id = new.billing_profile_id
        returning next_invoice_no - 1, invoice_prefix into new.number, prefix;
      new.number_text := prefix || new.number::text;
      new.sent_at := now();
    elsif new.status <> 'draft' then
      perform app.refuse('a draft invoice is sent before it is paid');
    end if;
    return new;
  end if;
  if (to_jsonb(new) - moving) is distinct from (to_jsonb(old) - moving) then
    perform app.refuse('a sent invoice never changes; issue a credit note');
  end if;
  if old.pdf_path is not null and new.pdf_path is distinct from old.pdf_path then
    perform app.refuse('a sent invoice never changes; issue a credit note');
  end if;
  if new.status is distinct from old.status
     and (old.status, new.status) not in (('sent', 'part_paid'), ('sent', 'paid'), ('part_paid', 'paid'),
                                          ('sent', 'written_off'), ('part_paid', 'written_off')) then
    perform app.refuse('that invoice status change is not allowed');
  end if;
  if old.status in ('paid', 'written_off') and new.paid_amount is distinct from old.paid_amount then
    perform app.refuse('a settled invoice never changes; issue a credit note');
  end if;
  return new;
end
$$;
revoke all on function app.invoices_guard() from public;
create trigger invoices_guard before insert or update on app.invoices
  for each row execute function app.invoices_guard();

-- Credit notes ----------------------------------------------------------------------------------------------
create table app.credit_notes (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  invoice_id uuid not null references app.invoices (id) on delete cascade,
  number integer check (number > 0),
  number_text text check (length(number_text) <= 40),
  amount numeric(12, 2) not null check (amount > 0),
  reason text not null check (reason ~ '^[a-z][a-z0-9_]{0,39}$'),
  issued_by uuid default app.current_person_id(),
  issued_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);
create index credit_notes_invoice on app.credit_notes (invoice_id);
create index credit_notes_case on app.credit_notes (case_id);

create function app.credit_notes_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  inv record;
  credited numeric;
  prefix text;
begin
  select i.* into inv from app.invoices i where i.id = new.invoice_id and i.case_id = new.case_id for update;
  if not found or inv.status = 'draft' then
    perform app.refuse('a credit note corrects a sent invoice of the case');
  end if;
  select coalesce(sum(amount), 0) into credited from app.credit_notes where invoice_id = new.invoice_id;
  if credited + new.amount > inv.total then
    perform app.refuse('credit notes cannot exceed the invoice total');
  end if;
  update app.billing_profiles set next_credit_note_no = next_credit_note_no + 1
    where id = inv.billing_profile_id
    returning next_credit_note_no - 1, invoice_prefix into new.number, prefix;
  new.number_text := prefix || 'CN' || new.number::text;
  new.issued_at := now();
  return new;
end
$$;
revoke all on function app.credit_notes_guard() from public;
create trigger credit_notes_guard before insert on app.credit_notes
  for each row execute function app.credit_notes_guard();

-- Access (rule 9) -------------------------------------------------------------------------------------------
alter table app.report_paragraphs enable row level security;
alter table app.citations enable row level security;
alter table app.final_check_runs enable row level security;
alter table app.report_snapshots enable row level security;
alter table app.report_servings enable row level security;
alter table app.spot_checks enable row level security;
alter table app.time_entries enable row level security;
alter table app.invoices enable row level security;
alter table app.credit_notes enable row level security;

create policy report_paragraphs_view on app.report_paragraphs for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy citations_view on app.citations for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy final_check_runs_view on app.final_check_runs for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy report_snapshots_view on app.report_snapshots for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy report_snapshots_add on app.report_snapshots for insert to app_api
  with check (app.can_access_case(case_id, 'report.sign'));
create policy report_servings_view on app.report_servings for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy report_servings_add on app.report_servings for insert to app_api
  with check (app.can_access_case(case_id, 'report.serve'));
create policy spot_checks_view on app.spot_checks for select to app_api
  using (app.can_access_case(case_id, 'report.build'));
create policy spot_checks_add on app.spot_checks for insert to app_api
  with check (app.can_access_case(case_id, 'report.build') and created_by = app.current_person_id());
create policy time_entries_view on app.time_entries for select to app_api
  using (app.can_access_case(case_id, 'case.view_content')
         and (person_id = app.current_person_id() or app.can_access_case(case_id, 'invoice.create')));
create policy time_entries_add on app.time_entries for insert to app_api
  with check (app.can_access_case(case_id, 'case.view_content') and person_id = app.current_person_id()
              and (not adjusted_by_expert or app.can_access_case(case_id, 'invoice.create')));
create policy time_entries_extend on app.time_entries for update to app_api
  using (person_id = app.current_person_id() and not adjusted_by_expert
         and app.can_access_case(case_id, 'case.view_content'))
  with check (person_id = app.current_person_id() and not adjusted_by_expert);
create policy invoices_view on app.invoices for select to app_api
  using (app.can_access_case(case_id, 'invoice.create') or app.can_access_case(case_id, 'invoice.send')
         or app.can_access_case(case_id, 'invoice.record_payment'));
create policy invoices_add on app.invoices for insert to app_api
  with check (app.can_access_case(case_id, 'invoice.create'));
create policy invoices_change on app.invoices for update to app_api
  using (app.can_access_case(case_id, 'invoice.create') or app.can_access_case(case_id, 'invoice.send')
         or app.can_access_case(case_id, 'invoice.record_payment'))
  with check (app.can_access_case(case_id, 'invoice.create') or app.can_access_case(case_id, 'invoice.send')
              or app.can_access_case(case_id, 'invoice.record_payment'));
create policy credit_notes_view on app.credit_notes for select to app_api
  using (app.can_access_case(case_id, 'invoice.create') or app.can_access_case(case_id, 'invoice.send')
         or app.can_access_case(case_id, 'invoice.record_payment'));
create policy credit_notes_add on app.credit_notes for insert to app_api
  with check (app.can_access_case(case_id, 'credit_note.issue'));

create policy report_paragraphs_job on app.report_paragraphs to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());
create policy citations_job on app.citations to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());
create policy final_check_runs_job on app.final_check_runs to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());
create policy report_snapshots_job on app.report_snapshots for select to app_worker
  using (case_id = app.current_job_case_id());
create policy report_servings_job on app.report_servings for select to app_worker
  using (case_id = app.current_job_case_id());
create policy spot_checks_job on app.spot_checks for select to app_worker
  using (case_id = app.current_job_case_id());
create policy time_entries_job on app.time_entries for select to app_worker
  using (case_id = app.current_job_case_id());
create policy invoices_job on app.invoices for select to app_worker
  using (case_id = app.current_job_case_id());
create policy credit_notes_job on app.credit_notes for select to app_worker
  using (case_id = app.current_job_case_id());

revoke update, delete on app.report_paragraphs, app.citations, app.final_check_runs, app.report_snapshots,
  app.report_servings, app.credit_notes from app_api, app_worker;
revoke insert on app.report_paragraphs, app.citations, app.final_check_runs from app_api;
revoke delete on app.spot_checks, app.time_entries, app.invoices from app_api, app_worker;
revoke update on app.spot_checks from app_api;

reset role;
