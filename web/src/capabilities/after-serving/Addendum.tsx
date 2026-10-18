// S21b Correct or update your report (docs/design/screens/S21b-Addendum.dc.html). The served version is shown
// read-only and never altered; changes go in a supplementary report that is signed behind the code.
import { useState } from "react";
import { useParams } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Checkbox, DictationBox, LinkButton, LoadError, Loading, Notice } from "../../foundation/ui";
import { declarationParts, Select, useWaiting } from "./shared";
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
    <div className="av-page av-stack">
      <LinkButton variant="quiet" to={`/cases/${caseId}/served`}>{w("back")}</LinkButton>
      <h2>{w("change.title")}</h2>
      <p>{w("change.intro")}</p>
      {served.data.version && (
        <section className="av-card av-stack" aria-labelledby="served-h">
          <h3 id="served-h">{w("change.served_locked", { name: served.data.version.display_name })}</h3>
          {paras.map((p) => <p key={p.id} className="av-after-words">{p.text}</p>)}
        </section>
      )}
      {!locked && <AddChange caseId={caseId} paras={paras} onDone={() => changes.reload()} />}
      <section className="av-card av-stack" aria-labelledby="list-h">
        <h3 id="list-h">{w("change.list")}</h3>
        {changes.data.changes.length === 0 && <p>{w("change.none")}</p>}
        <ul className="av-after-list">
          {changes.data.changes.map((c) => (
            <li key={c.id} className="av-after-item av-stack">
              <p className="av-after-words">{c.new_text}</p>
              {c.suggested_dependents.filter((d) => d.depends).map((d) => (
                <div key={d.paragraph_id}>
                  <p>{w("change.depends", { n: num(d.paragraph_id) })}</p>
                  <p className="av-muted">{w("change.depends_reason", { reason: d.reason })}</p>
                </div>
              ))}
            </li>
          ))}
        </ul>
      </section>
      {building.waiting && <Notice><p>{w("change.building")}</p></Notice>}
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {!locked && !sup && <div className="av-after-footer"><Button onClick={() => void build()}>{w("change.build")}</Button></div>}
      {!locked && sup && <Button variant="secondary" onClick={() => void build()}>{w("change.build")}</Button>}
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
    <section className="av-card av-stack" aria-labelledby="add-h">
      <h3 id="add-h">{w("change.add")}</h3>
      <Select label={w("change.paragraph")} value={paragraph} onChange={(e) => setParagraph(e.target.value)}>
        <option value="" />
        {paras.map((p) => <option key={p.id} value={p.id}>{w("served.paragraph", { n: p.para_no })}</option>)}
      </Select>
      {old && <section aria-label={w("change.said")} className="av-stack"><h4>{w("change.said")}</h4><p className="av-after-words">{old.text}</p></section>}
      <fieldset className="av-after-fieldset">
        <legend>{w("change.kind")}</legend>
        {KINDS.map((k) => (
          <label key={k} className="av-after-radio">
            <input type="radio" name="kind" value={k} checked={kind === k} onChange={() => setKind(k)} />
            <span>{w(`change.kind.${k}`)} {w(`change.kind.${k}_hint`)}</span>
          </label>
        ))}
      </fieldset>
      <DictationBox caseId={caseId} context="report_change" label={w("change.now")} value={text} onChange={setText} rows={5} />
      <DictationBox caseId={caseId} context="report_change_reason" label={w("change.reason")} value={reason} onChange={setReason} rows={3} />
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      <div className="av-after-footer"><Button variant="secondary" disabled={!ready} onClick={() => void add()}>{w("change.add")}</Button></div>
    </section>
  );
}

function Sign({ caseId, sup, declarations, onDone }: { caseId: string; sup: NonNullable<Schemas["app__features__after_serving__routes_changes__Changes"]["supplementary"]>; declarations: Schemas["CasePack"]["declarations"]; onDone: () => void }) {
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
