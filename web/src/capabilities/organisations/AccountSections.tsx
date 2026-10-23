// S28 sections that are slice O's, as registered screens linked from the account page: the records custodian
// ("If you can't carry on") and closing the account with the export status of each case.
import { useState, type FormEvent } from "react";
import { errorMessage } from "../../foundation/lib/errors";
import { screenPath } from "../../foundation/lib/screens";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, unwrap } from "../../foundation/platform-client";
import { BackLink, Button, Dialog, LoadError, Loading, TextField, TextLink } from "../../foundation/ui";
import { MessageNotice, OWN_ERRORS, useAct, type Message } from "./common";
import { w } from "./words";

/** The custodian form, as the S28 section and as the page body. */
export function CustodianBody() {
  const { act, message, setMessage } = useAct();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const n = name.trim();
    const m = email.trim();
    if (Boolean(n) !== Boolean(m)) return setMessage({ tone: "error", text: w("custodian.both") });
    await act(
      () => unwrap(api.PUT("/v1/me/custodian", { body: { name: n || null, email: m || null } })),
      (saved) => (saved.name ? w("custodian.saved", { name: saved.name }) : w("custodian.cleared")),
    );
  };
  const clear = async () => {
    const done = await act(() => unwrap(api.PUT("/v1/me/custodian", { body: { name: null, email: null } })), () => w("custodian.cleared"));
    if (done) {
      setName("");
      setEmail("");
    }
  };
  return (
    <div className="av-stack">
      <p>{w("custodian.intro")}</p>
      <MessageNotice message={message} />
      <form className="av-stack av-card av-org-card" onSubmit={(e) => void save(e)} noValidate>
        <TextField label={w("custodian.name")} name="name" autoComplete="off" value={name} onChange={(e) => setName(e.target.value)} />
        <TextField label={w("custodian.email")} name="email" type="email" autoComplete="off" value={email} onChange={(e) => setEmail(e.target.value)} />
        <p className="av-muted av-small">{w("custodian.note")}</p>
        <div className="av-row av-org-actions">
          <Button variant="secondary" onClick={() => void clear()}>
            {w("custodian.clear")}
          </Button>
          <Button type="submit">{w("custodian.save")}</Button>
        </div>
      </form>
    </div>
  );
}

export function Custodian() {
  return (
    <div className="av-page av-page--narrow av-stack">
      <BackLink to="/account">{w("account.back")}</BackLink>
      <h1>{w("custodian.title")}</h1>
      <CustodianBody />
    </div>
  );
}

/** The closing section, as the S28 section and as the page body. */
export function CloseAccountBody() {
  const stepUp = useStepUp();
  const preview = useResource(() => unwrap(api.GET("/v1/me/close")), []);
  const [asking, setAsking] = useState(false);
  const [message, setMessage] = useState<Message>(null);
  const [closed, setClosed] = useState(false);

  if (preview.error) {
    return <LoadError onRetry={preview.reload} />;
  }
  if (!preview.data) {
    return <Loading />;
  }
  const cases = preview.data.cases;
  const close = async () => {
    setMessage(null);
    try {
      const done = await stepUp(() => unwrap(api.POST("/v1/me/close")));
      if (done) {
        setClosed(true);
        setMessage({ tone: "success", text: w("close_account.done") });
      }
    } catch (err) {
      if (err instanceof ApiError && err.code === "export_first") {
        preview.reload(); // the list shows which cases still need their export
        setMessage({ tone: "error", text: w("close_account.export_first") });
      } else {
        setMessage({ tone: "error", text: errorMessage(err, OWN_ERRORS) });
      }
    } finally {
      setAsking(false);
    }
  };
  return (
    <div className="av-stack">
      <p>{w("close_account.intro")}</p>
      <MessageNotice message={message} />
      {closed && <p>{w("close_account.leaves")}</p>}
      {!closed && (
        <>
          <section className="av-card av-org-card" aria-labelledby="close-cases">
            <h2 id="close-cases">{w("close_account.cases")}</h2>
            {cases.length === 0 ? (
              <p className="av-muted">{w("close_account.none")}</p>
            ) : (
              <ul className="av-rows" role="list" aria-label={w("close_account.cases")}>
                {cases.map((c) => {
                  const to = screenPath("close", { caseId: c.case_id });
                  const status = c.exported ? w("close_account.exported") : w("close_account.needs_export");
                  return (
                    <li key={c.case_id} className="av-row-item">
                      <div className="av-row-item__text">
                        <p className="av-field__label">{c.label}</p>
                        <p className="av-muted">{status}</p>
                      </div>
                      {to && (
                        <TextLink to={to} aria-label={w("close_account.export_for", { label: c.label })}>
                          {w("close_account.export")}
                        </TextLink>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
          <div className="av-row av-org-actions">
            <Button variant="danger" onClick={() => setAsking(true)}>
              {w("close_account.button")}
            </Button>
          </div>
        </>
      )}
      {asking && (
        <Dialog title={w("close_account.dialog")} onClose={() => setAsking(false)}>
          <p>{w("close_account.dialog_body")}</p>
          <div className="av-row">
            <Button variant="danger" onClick={() => void close()}>
              {w("close_account.confirm")}
            </Button>
            <Button variant="secondary" onClick={() => setAsking(false)}>
              {w("close_account.keep")}
            </Button>
          </div>
        </Dialog>
      )}
    </div>
  );
}

export function CloseAccount() {
  return (
    <div className="av-page av-page--narrow av-stack">
      <BackLink to="/account">{w("account.back")}</BackLink>
      <h1>{w("close_account.title")}</h1>
      <CloseAccountBody />
    </div>
  );
}
