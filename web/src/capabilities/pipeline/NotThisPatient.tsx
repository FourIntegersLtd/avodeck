// Screen 30, not this patient's record: pages whose name or date of birth differ from the case. The
// named expert decides each one; set-aside pages can be undone until the API deletes them.
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { humanise } from "../../foundation/i18n";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, LinkButton, LoadError, Loading, Notice } from "../../foundation/ui";
import { can, casePath, partyWord } from "./data";
import { formatNumber } from "./format";
import { Thumbnail } from "./Thumbnail";
import { w, wn, wordFor } from "./words";

type Q = Schemas["QuarantinePage"];
type Choice = "not" | "is";
const SHOWN = new Set(["confirmed", "purged"]);

export function NotThisPatient() {
  const { item, pack } = useCaseFrame();
  const caseId = item.id;
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const asked = Number(search.get("page"));
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/bundle/summary", casePath(caseId))), [caseId]);
  const [pages, setPages] = useState<Q[] | null>(null);
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const decides = can(item, "fact.verify");
  const name = item.label;
  const num = (n: number) => formatNumber(n, pack.locale);
  const party = partyWord(pack, item);

  useEffect(() => {
    if (!found.data) return;
    const s = found.data;
    const listed = [...(s.quarantine_suggestions ?? []).filter((q) => q.status === "suggested"), ...(s.quarantined ?? []).filter((q) => SHOWN.has(q.status))];
    setPages(listed);
    if (!Number.isInteger(asked) || asked < 1 || listed.some((q) => q.page_no === asked)) return;
    let current = true;
    unwrap(api.GET("/v1/cases/{case_id}/pages/{page_no}", { params: { path: { case_id: caseId, page_no: asked } } }))
      .then((p) => current && setPages([...listed, { page_id: p.page_id, page_no: p.page_no, status: "suggested", reason: "", job_id: null }]))
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [found.data, asked, caseId]);

  if (found.error) return <div className="av-page"><LoadError onRetry={found.reload} /></div>;
  if (!pages) return <div className="av-page"><Loading /></div>;

  const pending = pages.filter((q) => q.status === "suggested");
  const notOurs = pending.filter((q) => choices[q.page_id] === "not");
  const ours = pending.filter((q) => choices[q.page_id] === "is");
  const onlySaving = notOurs.length === 0 && ours.length > 0;
  const path = (q: Q) => ({ params: { path: { case_id: caseId, page_id: q.page_id } } });
  const choose = (q: Q, c: Choice) => setChoices((all) => ({ ...all, [q.page_id]: c }));

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await Promise.all([
        ...notOurs.map((q) => unwrap(api.POST("/v1/cases/{case_id}/pages/{page_id}/quarantine", { ...path(q), body: { reason: q.reason } }))),
        ...ours.map((q) => unwrap(api.DELETE("/v1/cases/{case_id}/pages/{page_id}/quarantine", path(q)))),
      ]);
      const ask = notOurs.length > 0 && can(item, "request.draft");
      navigate(`/cases/${caseId}/bundle${ask ? "/ask" : ""}`);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  async function undo(q: Q) {
    setError(null);
    try {
      const changed = await unwrap(api.DELETE("/v1/cases/{case_id}/pages/{page_id}/quarantine", path(q)));
      setPages((all) => (all ?? []).map((p) => (p.page_id === q.page_id ? changed : p)));
      setNotes((all) => ({ ...all, [q.page_id]: w("quarantine.undone") }));
    } catch (e) {
      const text = e instanceof ApiError && e.status === 409 ? w("quarantine.purged") : errorMessage(e);
      setNotes((all) => ({ ...all, [q.page_id]: text }));
    }
  }

  return (
    <div className="av-page av-stack">
      <h1>{w("quarantine.title")}</h1>
      <p className="pl-lead">{w("quarantine.intro", { name })}</p>
      {!decides && <Notice><p>{w("quarantine.only_expert")}</p></Notice>}
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {pages.length === 0 ? (
        <p>{w("quarantine.none")}</p>
      ) : (
        <div className="pl-quarantine">
          <ul className="pl-plain pl-list">
            {pages.map((q) => (
              <li key={q.page_id} className="av-card pl-copy">
                <div className="pl-copy__pages">
                  <Thumbnail caseId={caseId} pageNo={q.page_no} alt={w("page_alt", { page: num(q.page_no) })} />
                </div>
                <div className="pl-copy__body">
                  <h2 className="pl-card__title av-mono">{w("page_ref", { page: num(q.page_no) })}</h2>
                  <p>{q.reason ? wordFor("quarantine.reason", q.reason, humanise(q.reason)) : ""}</p>
                  {q.status === "suggested" && decides && (
                    <div className="av-row">
                      <Button variant="secondary" className="pl-choice" aria-pressed={choices[q.page_id] === "not"} onClick={() => choose(q, "not")}>
                        {w("quarantine.not", { name })}
                      </Button>
                      <Button variant="secondary" className="pl-choice" aria-pressed={choices[q.page_id] === "is"} onClick={() => choose(q, "is")}>
                        {w("quarantine.is", { name })}
                      </Button>
                    </div>
                  )}
                  {q.status === "confirmed" && (
                    <div className="av-row">
                      <span className="av-badge">{w("quarantine.set_aside")}</span>
                      {decides && !notes[q.page_id] && (
                        <Button variant="quiet" onClick={() => undo(q)}>{w("quarantine.undo")}</Button>
                      )}
                    </div>
                  )}
                  {q.status === "purged" && <p>{w("quarantine.purged")}</p>}
                  {notes[q.page_id] && <p role="status">{notes[q.page_id]}</p>}
                </div>
              </li>
            ))}
          </ul>
          <section className="av-card pl-section">
            <h2>{w("quarantine.explain")}</h2>
            <ul className="pl-plain pl-explain">
              <li><p className="pl-strong">{w("quarantine.e_numbers")}</p><p>{w("quarantine.e_numbers_note")}</p></li>
              <li><p className="pl-strong">{w("quarantine.e_unused")}</p><p>{w("quarantine.e_unused_note")}</p></li>
              <li><p className="pl-strong">{party ? w("quarantine.e_told", { party }) : w("quarantine.e_told_anyone")}</p><p>{w("quarantine.e_told_note")}</p></li>
              <li><p className="pl-strong">{w("quarantine.e_deleted")}</p><p>{wn("quarantine.e_deleted_note", pack.quarantine_purge_days, pack.locale)}</p></li>
            </ul>
          </section>
        </div>
      )}
      {decides && pending.length > 0 && (
        <div className="pl-actions">
          <LinkButton variant="quiet" to={`/cases/${caseId}/bundle`}>{w("quarantine.later")}</LinkButton>
          <Button onClick={submit} disabled={busy || (notOurs.length === 0 && ours.length === 0)}>
            {onlySaving ? w("quarantine.save") : wn("quarantine.submit", notOurs.length, pack.locale)}
          </Button>
        </div>
      )}
    </div>
  );
}
