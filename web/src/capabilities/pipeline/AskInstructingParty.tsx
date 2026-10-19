// Screen 08b, ask the instructing party for what's missing. The API drafts the email from the pack's
// wording (patient details left out); the expert may edit it, the API notes the request in the case
// record, and the expert's own email opens with it.
import { useEffect, useState } from "react";
import { useSession } from "../../foundation/lib/session";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import {
  api,
  ApiError,
  idempotencyKey,
  unwrap,
} from "../../foundation/platform-client";
import {
  BackLink,
  Button,
  Checkbox,
  LoadError,
  Loading,
  Notice,
  TextField,
} from "../../foundation/ui";
import { casePath } from "./data";
import { formatDate, formatNumber } from "./format";
import { mail } from "./mail";
import { w, wn } from "./words";

export function AskInstructingParty() {
  const { item, pack } = useCaseFrame();
  const { me } = useSession();
  const caseId = item.id;
  const found = useResource(async () => {
    const path = casePath(caseId);
    const [draft, summary] = await Promise.all([
      unwrap(
        api.POST("/v1/cases/{case_id}/instructing-party-requests/draft", {
          ...path,
          body: { extension: false },
        }),
      ),
      unwrap(api.GET("/v1/cases/{case_id}/bundle/summary", path)),
    ]);
    return { draft, summary };
  }, [caseId]);
  const [to, setTo] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState(false);
  const [notice, setNotice] = useState<{
    tone: "success" | "error";
    text: string;
  } | null>(null);

  useEffect(() => {
    if (found.data) {
      setSubject(found.data.draft.subject);
      setBody(found.data.draft.body);
    }
  }, [found.data]);

  if (found.error)
    return (
      <div className="av-page">
        <LoadError onRetry={found.reload} />
      </div>
    );
  if (!found.data)
    return (
      <div className="av-page">
        <Loading />
      </div>
    );
  const { draft, summary } = found.data;
  const last = Math.max(
    summary.pages,
    ...summary.versions.map((v) => v.last_page_no ?? 0),
  );
  const setAside = (summary.quarantined ?? [])
    .filter((q) => q.status === "confirmed")
    .map((q) => q.page_id);

  async function send() {
    setBusy(true);
    setNotice(null);
    try {
      const address = to.trim();
      const logged = await unwrap(
        api.POST("/v1/cases/{case_id}/instructing-party-requests", {
          ...casePath(caseId),
          headers: idempotencyKey(),
          body: {
            ...(address ? { to: address } : {}),
            subject,
            body,
            missing_item_ids: draft.missing_item_ids,
            clearer_copy_page_ids: draft.clearer_copy_page_ids,
            quarantined_page_ids: setAside,
          },
        }),
      );
      mail.open(logged.mailto);
      setNotice({ tone: "success", text: w("ask.sent") });
    } catch (error) {
      const text =
        error instanceof ApiError && error.status === 422
          ? w("ask.check")
          : errorMessage(error);
      setNotice({ tone: "error", text });
    } finally {
      setBusy(false);
    }
  }

  async function askMore(on: boolean) {
    setNotice(null);
    try {
      const again = await unwrap(
        api.POST("/v1/cases/{case_id}/instructing-party-requests/draft", {
          ...casePath(caseId),
          body: { extension: on },
        }),
      );
      setBody(again.body);
      setMore(on);
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(`${subject}\n\n${body}`);
      setNotice({ tone: "success", text: w("ask.copied") });
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    }
  }

  return (
    <div className="av-page av-stack">
      <BackLink to={`/cases/${caseId}/bundle`}>{w("back")}</BackLink>
      <h1>{w("ask.title", { label: draft.label })}</h1>
      <p className="pl-lead">{w("ask.intro")}</p>
      <div className="pl-ask">
        <section className="av-card pl-letter" aria-label={w("ask.body")}>
          <TextField
            name="to"
            label={w("ask.to")}
            hint={w("ask.to_hint")}
            type="email"
            autoComplete="email"
            value={to}
            onChange={(e) => setTo(e.target.value)}
          />
          {editing ? (
            <div className="av-stack">
              <TextField
                name="subject"
                label={w("ask.subject")}
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
              />
              <div className="av-field">
                <label className="av-field__label" htmlFor="pl-ask-body">
                  {w("ask.body")}
                </label>
                <textarea
                  id="pl-ask-body"
                  className="av-textarea"
                  rows={12}
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                />
              </div>
              <div className="av-row">
                <Button variant="secondary" onClick={() => setEditing(false)}>
                  {w("ask.done_editing")}
                </Button>
              </div>
            </div>
          ) : (
            <>
              <dl className="pl-letter__head">
                {me.person.full_name && (
                  <div>
                    <dt>{w("ask.from")}</dt>
                    <dd>
                      {w("ask.from_usual", { name: me.person.full_name })}
                    </dd>
                  </div>
                )}
                <div>
                  <dt>{w("ask.subject")}</dt>
                  <dd className="pl-strong">{subject}</dd>
                </div>
              </dl>
              <p className="pl-letter__body">{body}</p>
              <div className="av-row">
                <Button variant="secondary" onClick={() => setEditing(true)}>
                  {w("ask.edit")}
                </Button>
              </div>
            </>
          )}
          <p className="pl-note">{w("ask.private")}</p>
        </section>
        <div className="av-stack">
          {draft.unreadable_document_names.length > 0 && (
            <section className="av-card pl-section">
              <h2>{w("ask.files")}</h2>
              <ul className="pl-steps-plain">
                {draft.unreadable_document_names.map((name, i) => (
                  <li key={draft.unreadable_document_ids[i] ?? name}>{name}</li>
                ))}
              </ul>
              <p>{w("ask.files_note")}</p>
            </section>
          )}
          <section className="av-card pl-section">
            <h2>{w("ask.next")}</h2>
            <ul className="pl-steps-plain">
              <li>{w("ask.next_noted")}</li>
              <li>
                {w("ask.next_arrive", {
                  last: formatNumber(last, pack.locale),
                })}
              </li>
              <li>{w("ask.next_keep")}</li>
            </ul>
          </section>
          {draft.extension && (
            <section className="av-card pl-section">
              <h2>{w("ask.more_time")}</h2>
              <p>
                {w("ask.more_time_due", {
                  due: formatDate(draft.extension.due_date, pack.locale),
                })}
              </p>
              <Checkbox
                label={wn(
                  "ask.more_time_add",
                  draft.extension.days,
                  pack.locale,
                  {
                    after: formatDate(draft.extension.after_date, pack.locale),
                  },
                )}
                checked={more}
                disabled={editing}
                onChange={(e) => void askMore(e.target.checked)}
              />
            </section>
          )}
        </div>
      </div>
      {notice && (
        <Notice tone={notice.tone}>
          <p>{notice.text}</p>
        </Notice>
      )}
      <div className="pl-actions">
        <Button variant="secondary" onClick={copy}>
          {w("ask.copy")}
        </Button>
        <Button onClick={send} disabled={busy}>
          {w("ask.send")}
        </Button>
      </div>
    </div>
  );
}
