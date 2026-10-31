// The support console's other look-ups (support plan phase 2; permissions.md "Avodeck staff"): a person by their
// sign-in email, a practice or team by its number, and how the service is running. Flags, counts and dates
// only: the server never sends a name or an email back, so nothing here can show one (rule 6).
import { useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { useSession } from "../../foundation/lib/session";
import { useStepUp } from "../../foundation/lib/stepUp";
import { api, ApiError, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, TextField } from "../../foundation/ui";
import { MessageNotice, OWN_ERRORS, type Message } from "./common";
import { w } from "./words";

type Person = Schemas["SupportPerson"];
type Organisation = Schemas["SupportOrganisation"];
type Cell = Schemas["SupportCellStatus"];

function when(iso: string, locale: string | null | undefined): string {
  try {
    return new Intl.DateTimeFormat(locale ?? undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section aria-labelledby={`support-${id}-title`} className="av-card av-org-card av-stack">
      <h2 id={`support-${id}-title`}>{title}</h2>
      {children}
    </section>
  );
}

/** Runs a console call; a 404 means the person is not staff, which the console page shows as not found. */
function useLookup<T>(onHidden: () => void) {
  const stepUp = useStepUp();
  const [message, setMessage] = useState<Message>(null);
  const run = async (call: () => Promise<T>): Promise<T | undefined> => {
    setMessage(null);
    try {
      return await stepUp(call);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        onHidden();
        return undefined;
      }
      setMessage({ tone: "error", text: errorMessage(err, { ...OWN_ERRORS, server_error: "organisations.console.error" }) });
      return undefined;
    }
  };
  return { run, message };
}

export function SupportLookups({ onHidden }: { onHidden: () => void }) {
  return (
    <>
      <PersonLookup onHidden={onHidden} />
      <OrganisationLookup onHidden={onHidden} />
      <CellCheck onHidden={onHidden} />
    </>
  );
}

function PersonLookup({ onHidden }: { onHidden: () => void }) {
  const { me } = useSession();
  const locale = me.person.locale;
  const { run, message } = useLookup<Schemas["SupportPersonList"]>(onHidden);
  const [email, setEmail] = useState("");
  const [people, setPeople] = useState<Person[] | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return;
    const found = await run(() => unwrap(api.GET("/v1/support/people", { params: { query: { q: email.trim() } } })));
    if (found) setPeople(found.people);
  };
  return (
    <Section id="person" title={w("console.person.title")}>
      <form className="av-row av-org-search" onSubmit={(e) => void submit(e)} noValidate>
        <TextField label={w("console.person.query")} name="person-email" type="email" autoComplete="off" value={email} onChange={(e) => setEmail(e.target.value)} />
        <Button type="submit">{w("console.person.search")}</Button>
      </form>
      <MessageNotice message={message} />
      {people && people.length === 0 && <p className="av-muted">{w("console.person.none")}</p>}
      {people?.map((p) => (
        <div key={p.person_id} className="av-stack">
          <p className="av-field__label">{p.person_id}</p>
          {p.closed_at && <p>{w("console.person.closed", { when: when(p.closed_at, locale) })}</p>}
          <p>{p.mfa_enrolled === null ? w("console.person.mfa_unknown") : p.mfa_enrolled ? w("console.person.mfa_yes") : w("console.person.mfa_no")}</p>
          {p.mfa_locked_until && <p>{w("console.person.locked", { when: when(p.mfa_locked_until, locale) })}</p>}
          <p>{w("console.person.codes", { count: p.recovery_codes_left })}</p>
          {p.last_mfa_at && <p className="av-muted">{w("console.person.last_mfa", { when: when(p.last_mfa_at, locale) })}</p>}
          {p.pending_email_change && <p>{w("console.person.email_change")}</p>}
          <p>{p.custodian_set ? w("console.person.custodian_yes") : w("console.person.custodian_no")}</p>
          {p.blocking_case_ids_for_closure.length > 0 && (
            <>
              <p>{w("console.person.blocking", { count: p.blocking_case_ids_for_closure.length })}</p>
              <ul className="av-stack" role="list">
                {p.blocking_case_ids_for_closure.map((id) => (
                  <li key={id}>
                    <Link to={`/support/cases/${id}`}>{id}</Link>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      ))}
    </Section>
  );
}

function OrganisationLookup({ onHidden }: { onHidden: () => void }) {
  const { me } = useSession();
  const locale = me.person.locale;
  const { run, message } = useLookup<Organisation>(onHidden);
  const [id, setId] = useState("");
  const [org, setOrg] = useState<Organisation | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!id.trim()) return;
    const found = await run(() => unwrap(api.GET("/v1/support/organisations/{organisation_id}", { params: { path: { organisation_id: id.trim() } } })));
    if (found) setOrg(found);
  };
  return (
    <Section id="org" title={w("console.org.title")}>
      <form className="av-row av-org-search" onSubmit={(e) => void submit(e)} noValidate>
        <TextField label={w("console.org.query")} name="organisation-id" autoComplete="off" value={id} onChange={(e) => setId(e.target.value)} />
        <Button type="submit">{w("console.org.search")}</Button>
      </form>
      <MessageNotice message={message} />
      {org && (
        <div className="av-stack">
          <p className="av-field__label">{org.organisation_id}</p>
          <p>{w("console.org.counts", { owners: org.owner_count, open: org.open_case_count })}</p>
          {org.closed_at && <p>{w("console.org.closed", { when: when(org.closed_at, locale) })}</p>}
          <h3>{w("console.org.members")}</h3>
          <ul className="av-stack" role="list">
            {org.members.map((m) => (
              <li key={`${m.person_id}-${m.started_at}`}>
                {m.ended_at
                  ? w("console.org.member_left", { role: w(`role.${m.role}`), when: when(m.ended_at, locale) })
                  : w("console.org.member", { role: w(`role.${m.role}`), when: when(m.started_at, locale) })}
              </li>
            ))}
          </ul>
          <h3>{w("console.org.invites")}</h3>
          {org.invites.length === 0 && <p className="av-muted">{w("console.case.none")}</p>}
          <ul className="av-stack" role="list">
            {org.invites.map((i) => (
              <li key={i.id}>{w("console.org.invite", { role: w(`role.${i.role}`), state: w(`console.org.invite_state.${i.state}`), when: when(i.sent_at, locale) })}</li>
            ))}
          </ul>
          <h3>{w("console.case.named.title")}</h3>
          {org.reassignments.length === 0 && <p className="av-muted">{w("console.case.none")}</p>}
          <ul className="av-stack" role="list">
            {org.reassignments.map((r) => (
              <li key={`${r.case_id}-${r.changed_at}`}>
                <Link to={`/support/cases/${r.case_id}`}>{r.case_id}</Link> {w("console.org.reassigned", { when: when(r.changed_at, locale) })}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Section>
  );
}

function CellCheck({ onHidden }: { onHidden: () => void }) {
  const { run, message } = useLookup<Cell>(onHidden);
  const [cell, setCell] = useState<Cell | null>(null);
  const check = async () => {
    const found = await run(() => unwrap(api.GET("/v1/support/cell/status")));
    if (found) setCell(found);
  };
  return (
    <Section id="cell" title={w("console.cell.title")}>
      <Button type="button" variant="secondary" onClick={() => void check()}>
        {w("console.cell.check")}
      </Button>
      <MessageNotice message={message} />
      {cell && (
        <div className="av-stack" role="status">
          <p>{cell.speech_ok ? w("console.cell.speech_ok") : w("console.cell.speech_no")}</p>
          <p>{cell.key_service_ok ? w("console.cell.keys_ok") : w("console.cell.keys_no")}</p>
          <p>
            {cell.worker_heartbeat_age_seconds === null
              ? w("console.cell.worker_none")
              : w("console.cell.worker", { seconds: cell.worker_heartbeat_age_seconds })}
          </p>
          <p>{w("console.cell.queue", { count: cell.queue_depth })}</p>
          <p>{w("console.cell.paused", { count: cell.paused_jobs })}</p>
          {cell.paused_jobs > 0 && <p>{w("console.case.jobs.paused_explain")}</p>}
        </div>
      )}
    </Section>
  );
}
