// S21b Correct or update your report (docs/design/screens/S21b-Addendum.dc.html). The served version is shown
// read-only and never altered; changes go in a supplementary report that is signed behind the code.
import { useState } from "react";
import { useParams } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { BackLink, Button, Checkbox, DictationBox, LoadError, Loading, Notice } from "../../foundation/ui";
import { TickGreenIcon } from "./icons";
import { declarationParts, longDate, Select, useLocale, useWaiting } from "./shared";
import { w } from "./words";

const KINDS = ["correction", "change_of_opinion"] as const;
const ERRORS: Record<string, string> = {
  stale: "after_serving.error.stale",
  invalid: "after_serving.error.invalid",
  no_changes: "after_serving.change.error.no_changes",
  already_signed: "after_serving.change.error.already_signed",
  locked: "after_serving.change.error.locked",
  declaration_confirmed: "after_serving.change.error.declaration_confirmed",
};

export function AddendumScreen() {
  const { caseId = "" } = useParams();
  const { pack } = useCaseFrame();
  const locale = useLocale();
  const [adding, setAdding] = useState(false);
  const [full, setFull] = useState(false);
  const path = { params: { path: { case_id: caseId } } };
  const served = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report/served", path)), [caseId]);
  const changes = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/report-changes", path)), [caseId]);
  const building = useWaiting(() => changes.reload(), changes.data?.supplementary?.id);
  const [error, setError] = useState<string | null>(null);

  if (served.error || changes.error) return <div className="av-page"><LoadError onRetry={() => { served.reload(); changes.reload(); }} /></div>;
  if (!served.data || !changes.data) return <div className="av-page"><Loading /></div>;
  const sup = changes.data.supplementary;
  const locked = sup?.status === "locked";
  const paras = served.data.paragraphs;
  const num = (id: string) => paras.find((p) => p.id === id)?.para_no ?? "";
  const list = changes.data.changes;
  const changedNos = [...new Set(list.map((c) => num(c.paragraph_id)))].filter((n) => n !== "");
  const dependentNos = [...new Set(list.flatMap((c) => c.suggested_dependents.filter((d) => d.depends).map((d) => num(d.paragraph_id))))]
    .filter((n) => n !== "" && !changedNos.includes(n));
  const servedOn = served.data.version?.served_on;
  const build = async () => {
    setError(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/report-changes/supplementary", path));
      building.start();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };

  return (
    <div className="av-page av-after-page av-after-page--full av-after-page--board av-stack">
      <div className="av-after-head">
        <div>
          <BackLink to={`/cases/${caseId}/served`}>{w("back")}</BackLink>
          <h2 className="av-after-title">{w("change.title")}</h2>
          <p className="av-muted av-after-lede">{w("change.intro")}</p>
        </div>
      </div>
      <div className="av-after-twocol">
        <section className="av-after-main av-stack">
          {served.data.version && (
            <details className="av-after-disclosure">
              <summary>{w("change.served_locked", { name: served.data.version.display_name })}</summary>
              <div className="av-card av-stack" aria-label={w("change.served")} role="region">
                {paras.map((p) => <p key={p.id} className="av-after-words">{p.text}</p>)}
              </div>
            </details>
          )}
          {!locked && (list.length === 0 || adding) && <AddChange caseId={caseId} paras={paras} onDone={() => { setAdding(false); changes.reload(); }} />}
          <section className="av-stack" aria-labelledby="list-h">
            <h3 id="list-h" className="av-visually-hidden">{w("change.list")}</h3>
            {list.length === 0 && <p>{w("change.none")}</p>}
            {list.map((c, index) => (
              <article key={c.id} className="av-after-pcard av-stack" aria-labelledby={`change-${c.id}`}>
                <div className="av-after-qcard__tags">
                  <h4 id={`change-${c.id}`}>{w("served.paragraph", { n: num(c.paragraph_id) })}</h4>
                  <span className="av-after-aboutchip">{w(`change.kind.${c.kind as (typeof KINDS)[number]}`)}</span>
                </div>
                <div className="av-stack">
                  <h5 className="av-after-label">{w("change.said")}</h5>
                  <p className="av-after-words av-after-struck"><del>{c.old_text}</del></p>
                </div>
                <div className="av-stack">
                  <h5 className="av-after-label av-after-label--green">{w("change.now")}</h5>
                  <p className="av-after-words av-after-now">{c.new_text}</p>
                </div>
                <div className="av-stack">
                  <h5 className="av-after-label">{w("change.why")}</h5>
                  <p className="av-after-words">{c.reason}</p>
                </div>
                {c.suggested_dependents.filter((d) => d.depends).map((d) => (
                  <div key={d.paragraph_id}>
                    <p>{w("change.depends", { n: num(d.paragraph_id) })}</p>
                    <p className="av-muted">{w("change.depends_reason", { reason: d.reason })}</p>
                  </div>
                ))}
                {!locked && !adding && index === list.length - 1 && (
                  <div><button type="button" className="av-after-linkbtn" onClick={() => setAdding(true)}>{w("change.another")}</button></div>
                )}
              </article>
            ))}
          </section>
        </section>
        {list.length > 0 && (
          <div className="av-after-side av-stack">
            <aside className="av-after-panel av-after-panel--side av-stack" aria-label={w("change.checked")}>
              <h3>{w("change.checked")}</h3>
              <ul className="av-after-ticks">
                <li><TickGreenIcon />{dependentNos.length > 0
                  ? w("change.checked_dependents", { changed: changedNos.join(", "), dependents: dependentNos.join(", ") })
                  : w("change.checked_none", { changed: changedNos.join(", ") })}</li>
                <li><TickGreenIcon />{w("change.checked_reason")}</li>
                <li><TickGreenIcon />{w("change.declaration_note")}</li>
              </ul>
            </aside>
            <section className="av-after-paper av-stack" aria-labelledby="summary-h">
              <h3 id="summary-h">{w("change.supplementary")}</h3>
              <p>{w("change.summary_changes", { paragraphs: changedNos.join(", ") })}</p>
              {servedOn && <p>{w("change.summary_refers", { date: longDate(servedOn, locale) })}</p>}
              {sup?.signed_by && sup.signed_on && <p>{w("change.summary_signed", { name: sup.signed_by, date: longDate(sup.signed_on, locale) })}</p>}
            </section>
            <p className="av-after-tellnote">{w("change.tell_note")}</p>
          </div>
        )}
      </div>
      {full && (
        <section className="av-card av-stack" aria-labelledby="full-h">
          <h3 id="full-h">{w("change.full")}</h3>
          {servedOn && <p className="av-muted">{w("change.summary_refers", { date: longDate(servedOn, locale) })}</p>}
          {list.map((c) => (
            <div key={c.id} className="av-stack">
              <strong className="av-after-mono">{w("served.paragraph", { n: num(c.paragraph_id) })}</strong>
              <p className="av-after-words">{c.new_text}</p>
              <p className="av-after-words av-muted">{c.reason}</p>
            </div>
          ))}
        </section>
      )}
      {building.waiting && <Notice><p>{w("change.building")}</p></Notice>}
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      <div className="av-after-footer">
        {list.length > 0 && <Button variant="secondary" aria-expanded={full} onClick={() => setFull(!full)}>{w("change.read_full")}</Button>}
        {!locked && <Button variant={sup ? "secondary" : "primary"} arrow={!sup} onClick={() => void build()}>{w("change.build")}</Button>}
      </div>
      {sup && <Sign caseId={caseId} sup={sup} declarations={pack.declarations} onDone={() => changes.reload()} />}
    </div>
  );
}

function AddChange({ caseId, paras, onDone }: { caseId: string; paras: Schemas["ServedParagraph"][]; onDone: () => void }) {
  const [paragraph, setParagraph] = useState("");
  const [kind, setKind] = useState("");
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const old = paras.find((p) => p.id === paragraph);
  const ready = !!paragraph && !!kind && !!text.trim() && !!reason.trim();
  const add = async () => {
    setError(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/report-changes", { params: { path: { case_id: caseId } }, body: { paragraph_id: paragraph, kind: kind as (typeof KINDS)[number], new_text: text, reason } }));
      setParagraph(""); setKind(""); setText(""); setReason("");
      onDone();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  return (
    <section className="av-stack av-after-form" aria-label={w("change.add")}>
      <div role="radiogroup" aria-label={w("change.kind")} className="av-after-kinds">
        {KINDS.map((k) => (
          <label key={k} className="av-after-kind">
            <input type="radio" name="kind" value={k} checked={kind === k} onChange={() => setKind(k)} />
            <span>
              <strong>{w(`change.kind.${k}`)}</strong>
              <span className="av-muted">{w(`change.kind.${k}_hint`)}</span>
            </span>
          </label>
        ))}
      </div>
      <div className="av-after-pcard av-stack">
        <Select label={w("change.paragraph")} value={paragraph} onChange={(e) => setParagraph(e.target.value)}>
          <option value="" />
          {paras.map((p) => <option key={p.id} value={p.id}>{w("served.paragraph", { n: p.para_no })}</option>)}
        </Select>
        {old && <section aria-label={w("change.said")} className="av-stack"><h4 className="av-after-label">{w("change.said")}</h4><p className="av-after-words av-after-struck"><del>{old.text}</del></p></section>}
        <DictationBox bar caseId={caseId} context="report_change" label={w("change.now")} value={text} onChange={setText} rows={5} />
        <DictationBox bar caseId={caseId} context="report_change_reason" label={w("change.reason")} value={reason} onChange={setReason} rows={3} />
        {error && <Notice tone="error"><p>{error}</p></Notice>}
        <div className="av-after-footer"><Button variant="secondary" disabled={!ready} onClick={() => void add()}>{w("change.add")}</Button></div>
      </div>
    </section>
  );
}

function Sign({ caseId, sup, declarations, onDone }: { caseId: string; sup: NonNullable<Schemas["ReportChanges"]["supplementary"]>; declarations: Schemas["CasePack"]["declarations"]; onDone: () => void }) {
  const stepUp = useStepUp();
  const [confirmed, setConfirmed] = useState(false);
  const [signed, setSigned] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const locked = sup.status === "locked";
  const sign = async () => {
    setError(null);
    try {
      const out = await stepUp(() => unwrap(api.POST("/v1/cases/{case_id}/report/versions/{version_id}/sign", { params: { path: { case_id: caseId, version_id: sup.id } }, body: { declaration_confirmed: true } })));
      if (out) { setSigned(true); onDone(); }
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  return (
    <section className="av-card av-stack" aria-labelledby="sup-h">
      <h3 id="sup-h">{w(locked ? "change.supplementary_locked" : "change.supplementary_draft", { name: sup.display_name })}</h3>
      {signed || locked ? (
        signed && <Notice tone="success"><p>{w("change.signed")}</p></Notice>
      ) : (
        <>
          <p>{w("change.declaration_note")}</p>
          {declarationParts(declarations).map((part) => <p key={part} className="av-after-words">{part}</p>)}
          <Checkbox label={w("change.confirm")} checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
          {error && <Notice tone="error"><p>{error}</p></Notice>}
          <div className="av-after-footer"><Button disabled={!confirmed} onClick={() => void sign()}>{w("change.sign")}</Button></div>
        </>
      )}
    </section>
  );
}
