// Screen 30, not this patient's record: pages whose name or date of birth differ from the case. The
// named expert decides each one; set-aside pages can be undone until the API deletes them.
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { humanise } from "../../foundation/i18n";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, unwrap, type Schemas } from "../../foundation/platform-client";
import { BackLink, Button, LoadError, Loading, Notice, PageRef, SegmentedControl, TextLink } from "../../foundation/ui";
import { can, casePath, partyWord } from "./data";
import { formatNumber } from "./format";
import { Thumbnail } from "./Thumbnail";
import { w, wn, wordFor } from "./words";

type Q = Schemas["QuarantinePage"];
type Choice = "not" | "is";

// The reason code sent when the expert sets aside a page they opened themselves (?page=).
const EXPERT_REASON = "not_this_person";

export function NotThisPatient() {
  const { item, pack } = useCaseFrame();
  const caseId = item.id;
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const asked = Number(search.get("page"));
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/bundle/summary", casePath(caseId))), [caseId], { cache: "pipeline/NotThisPatient#NotThisPatient:found" });
  const [pages, setPages] = useState<Q[] | null>(null);
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lookup, setLookup] = useState<string | null>(null);
  const decides = can(item, "fact.verify");
  const name = item.label;
  const num = (n: number) => formatNumber(n, pack.locale);
  const party = partyWord(pack, item);

  useEffect(() => {
    if (!found.data) return;
    const s = found.data;
    const listed = s.quarantine_listed ?? [];
    setPages(listed);
    setLookup(null);
    if (!search.has("page") || listed.some((q) => q.page_no === asked)) return;
    if (!Number.isInteger(asked) || asked < 1) {
      setLookup(w("quarantine.page_missing", { page: String(search.get("page")) }));
      return;
    }
    let current = true;
    unwrap(api.GET("/v1/cases/{case_id}/pages/{page_no}", { params: { path: { case_id: caseId, page_no: asked } } }))
      .then((p) => current && setPages([...listed, { page_id: p.page_id, page_no: p.page_no, status: "suggested", reason: "", job_id: null }]))
      .catch((e) => current && setLookup(e instanceof ApiError && e.status === 404 ? w("quarantine.page_missing", { page: formatNumber(asked, pack.locale) }) : errorMessage(e)));
    return () => {
      current = false;
    };
  }, [found.data, asked, caseId, search, pack.locale]);

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
        ...notOurs.map((q) => unwrap(api.POST("/v1/cases/{case_id}/pages/{page_id}/quarantine", { ...path(q), body: { reason: q.reason || EXPERT_REASON } }))),
        // A page opened with ?page= has no stored suggestion (reason ""), so keeping it needs no call.
        ...ours.filter((q) => q.reason).map((q) => unwrap(api.DELETE("/v1/cases/{case_id}/pages/{page_id}/quarantine", path(q)))),
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
    <div className="av-page pl-quarantine-page">
      <header className="pl-ask-head">
        <BackLink to={`/cases/${caseId}/bundle`}>{w("back")}</BackLink>
        <h1>{w("quarantine.title")}</h1>
        <p className="pl-lead">{w("quarantine.intro", { name })}</p>
      </header>
      {!decides && <Notice><p>{w("quarantine.only_expert")}</p></Notice>}
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {lookup && <Notice tone="error"><p role="alert">{lookup}</p></Notice>}
      {pages.length === 0 ? (
        <p>{lookup && search.has("page") ? w("quarantine.page_not_waiting", { page: num(asked) }) : w("quarantine.none")}</p>
      ) : (
        <>
          <ul className="pl-plain pl-grid">
            {pages.map((q) => (
              <li key={q.page_id} className="av-card pl-pagecard">
                <div className="pl-pagecard__picture">
                  <Thumbnail caseId={caseId} pageNo={q.page_no} alt={w("page_alt", { page: num(q.page_no) })} className="pl-page-full" full={q.status === "suggested"} />
                </div>
                <div className="pl-pagecard__decide">
                  <div className="pl-pagecard__meta">
                    <h2 className="pl-pagecard__page"><PageRef page={q.page_no} /></h2>
                    {q.reason && <span className="pl-chip">{wordFor("quarantine.reason", q.reason, humanise(q.reason))}</span>}
                  </div>
                  {q.status === "suggested" && decides && (
                    <SegmentedControl
                      label={w("quarantine.choice", { page: num(q.page_no) })}
                      value={choices[q.page_id] ?? null}
                      onChange={(v) => choose(q, v as Choice)}
                      options={[
                        { value: "not", label: w("quarantine.not", { name }) },
                        { value: "is", label: w("quarantine.is", { name }) },
                      ]}
                    />
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
          <section className="av-card pl-explain-card">
            <h2>{w("quarantine.explain")}</h2>
            <ul className="pl-plain pl-explain">
              <li><span className="pl-strong">{w("quarantine.e_numbers")}</span> <span>{w("quarantine.e_numbers_note")}</span></li>
              <li><span className="pl-strong">{w("quarantine.e_unused")}</span> <span>{w("quarantine.e_unused_note")}</span></li>
              <li><span className="pl-strong">{party ? w("quarantine.e_told", { party }) : w("quarantine.e_told_anyone")}</span> <span>{w("quarantine.e_told_note")}</span></li>
              <li><span className="pl-strong">{w("quarantine.e_deleted")}</span> <span>{wn("quarantine.e_deleted_note", pack.quarantine_purge_days, pack.locale)}</span></li>
            </ul>
          </section>
        </>
      )}
      {decides && pending.length > 0 && (
        <div className="pl-actions pl-actions--split">
          <TextLink to={`/cases/${caseId}/bundle`}>{w("quarantine.later")}</TextLink>
          <Button onClick={submit} disabled={busy || (notOurs.length === 0 && ours.length === 0)}>
            {onlySaving ? w("quarantine.save") : wn("quarantine.submit", notOurs.length, pack.locale)}
          </Button>
        </div>
      )}
    </div>
  );
}
