// Support creates an account for a new person (permissions.md "Avodeck staff"; migrations 1012, 1013). Staff
// only: the server answers anyone else with 404, which shows the not found page. Avodeck makes a temporary
// password (ADR 0024), shown here once to tell the person by phone or in person; they choose their own at
// first sign-in. No support action asks for a new code (ADR 0023).
import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError, idempotencyKey, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, TextField } from "../../foundation/ui";
import { MessageNotice, NotFoundPage, useAct, type Message } from "./common";
import { w } from "./words";

type Account = Schemas["SupportAccount"];
type Made = Schemas["SupportAccountWithPassword"];
type Shown = { email: string; password: string; renewed: boolean };
const OWN = {
  conflict: "organisations.accounts.taken",
  invalid_email: "organisations.accounts.check_email",
  too_many_attempts: "organisations.accounts.limit",
  sent_recently: "organisations.accounts.wait",
};

export function SupportAccounts() {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [hidden, setHidden] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const key = useRef<string | null>(null);
  const create = useAct(OWN, { stepUp: false });
  const renew = useAct(OWN, { stepUp: false });
  const [shown, setShown] = useState<Shown | null>(null);

  useEffect(() => {
    let live = true;
    unwrap(api.GET("/v1/support/accounts"))
      .then((found) => live && setAccounts(found.accounts))
      .catch((err: unknown) => live && (err instanceof ApiError && err.status === 404 ? setHidden(true) : setAccounts([])));
    return () => {
      live = false;
    };
  }, []);

  if (hidden) return <NotFoundPage />;
  const changed = (set: (v: string) => void) => (e: { target: { value: string } }) => {
    key.current = null; // a different form is a different request
    set(e.target.value);
  };
  // The password goes on screen only; the list keeps the account without it.
  const keep = (made: Made, renewed: boolean, notice: { setMessage: (m: Message) => void }) => {
    const password = made.temporary_password;
    const row: Account = { id: made.id, person_id: made.person_id, email: made.email, status: made.status, created_at: made.created_at, last_sent_at: made.last_sent_at };
    setAccounts((all) => (all?.some((a) => a.id === row.id) ? all.map((a) => (a.id === row.id ? row : a)) : [row, ...(all ?? [])]));
    if (password) setShown({ email: made.email, password, renewed });
    else {
      setShown(null);
      notice.setMessage({ tone: "success", text: w("accounts.already_shown") });
    }
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setShown(null);
    key.current ??= idempotencyKey()["Idempotency-Key"];
    const headers = { "Idempotency-Key": key.current };
    const body = { full_name: name.trim(), email: email.trim() };
    const made = await create.act(() => unwrap(api.POST("/v1/support/accounts", { body, params: { header: headers } })));
    if (made) {
      key.current = null;
      setName("");
      setEmail("");
      keep(made, false, create);
    }
  };
  const newPassword = async (account: Account) => {
    setShown(null);
    const path = { account_id: account.id };
    const made = await renew.act(() => unwrap(api.POST("/v1/support/accounts/{account_id}/temporary-password", { params: { path, header: idempotencyKey() } })));
    if (made) keep(made, true, renew);
  };
  return (
    <div className="av-page av-page--narrow av-stack">
      <h1>{w("accounts.title")}</h1>
      <p>{w("accounts.intro")}</p>
      <MessageNotice message={create.message} />
      {shown && <PasswordBox shown={shown} />}
      <form className="av-stack" onSubmit={(e) => void submit(e)} noValidate>
        <TextField label={w("accounts.name")} name="full_name" autoComplete="off" value={name} onChange={changed(setName)} />
        <TextField label={w("accounts.email")} name="email" type="email" autoComplete="off" value={email} onChange={changed(setEmail)} />
        <Button type="submit" busy={create.busy}>{w("accounts.create")}</Button>
      </form>
      <h2>{w("accounts.list")}</h2>
      <MessageNotice message={renew.message} />
      {accounts && accounts.length === 0 && <p className="av-muted">{w("accounts.none")}</p>}
      {accounts && accounts.length > 0 && (
        <ul className="av-rows av-org-results" role="list" aria-label={w("accounts.list")}>
          {accounts.map((a) => (
            <li key={a.id} className="av-row-item av-card av-org-result">
              <div className="av-row-item__text">
                <p className="av-field__label">{a.email}</p>
                <p className="av-muted">{a.status === "active" ? w("accounts.status.active") : w("accounts.status.invited")}</p>
              </div>
              {a.status === "invited" && (
                <Button variant="secondary" busy={renew.busy} onClick={() => void newPassword(a)}>
                  {w("accounts.renew")}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The temporary password, once, large and easy to copy or read aloud. */
function PasswordBox({ shown }: { shown: Shown }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(shown.password);
      setCopied(true);
    } catch {
      setCopied(false); // the person can still write it down
    }
  };
  return (
    <section className="av-card av-stack av-org-password" aria-label={w("accounts.password_for", { email: shown.email })}>
      <h2>{w("accounts.password_title")}</h2>
      {shown.renewed && <p>{w("accounts.old_stops")}</p>}
      <p className="av-mono av-org-password__value">{shown.password}</p>
      <p>{w("accounts.password_warning")}</p>
      <Button variant="secondary" onClick={() => void copy()}>{w("accounts.copy")}</Button>
      <p role="status" className="av-muted">{copied ? w("accounts.copied") : ""}</p>
    </section>
  );
}
