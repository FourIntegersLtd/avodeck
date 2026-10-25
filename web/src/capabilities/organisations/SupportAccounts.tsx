// Support creates an account for a new person (permissions.md "Avodeck staff"; migration 1012). Staff only:
// the server answers anyone else with 404, which shows the not found page. There is no password here: the
// person gets an email with a link to set their own, then sets up their code at first sign-in.
import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError, idempotencyKey, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, TextField } from "../../foundation/ui";
import { MessageNotice, NotFoundPage, useAct } from "./common";
import { w } from "./words";

type Account = Schemas["SupportAccount"];
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
  const create = useAct(OWN);
  const resend = useAct(OWN);

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
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    key.current ??= idempotencyKey()["Idempotency-Key"];
    const headers = { "Idempotency-Key": key.current };
    const body = { full_name: name.trim(), email: email.trim() };
    const made = await create.act(
      () => unwrap(api.POST("/v1/support/accounts", { body, params: { header: headers } })),
      (a) => w("accounts.created", { email: a.email }),
    );
    if (made) {
      key.current = null;
      setName("");
      setEmail("");
      setAccounts((all) => [made, ...(all ?? []).filter((a) => a.id !== made.id)]);
    }
  };
  const sendAgain = async (account: Account) => {
    const sent = await resend.act(
      () => unwrap(api.POST("/v1/support/accounts/{account_id}/resend", { params: { path: { account_id: account.id }, header: idempotencyKey() } })),
      (a) => w("accounts.resent", { email: a.email }),
    );
    if (sent) setAccounts((all) => (all ?? []).map((a) => (a.id === sent.id ? sent : a)));
  };
  return (
    <div className="av-page av-page--narrow av-stack">
      <h1>{w("accounts.title")}</h1>
      <p>{w("accounts.intro")}</p>
      <MessageNotice message={create.message} />
      <form className="av-stack" onSubmit={(e) => void submit(e)} noValidate>
        <TextField label={w("accounts.name")} name="full_name" autoComplete="off" value={name} onChange={changed(setName)} />
        <TextField label={w("accounts.email")} name="email" type="email" autoComplete="off" value={email} onChange={changed(setEmail)} />
        <Button type="submit" busy={create.busy}>{w("accounts.create")}</Button>
      </form>
      <h2>{w("accounts.list")}</h2>
      <MessageNotice message={resend.message} />
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
                <Button variant="secondary" busy={resend.busy} onClick={() => void sendAgain(a)}>
                  {w("accounts.resend")}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
