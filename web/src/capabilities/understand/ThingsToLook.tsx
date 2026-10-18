// S13 Things to look at. Code found these (the API's checks over checked facts); the expert decides each one.
// A reason is required for "not relevant" and "explained elsewhere". Nothing closes on its own.
import { useEffect, useState } from "react";
import { humanise, t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Checkbox, LoadError, Loading, Notice } from "../../foundation/ui";
import { useParams } from "react-router-dom";
import { casePath, pageLabel, TextArea } from "./shared";
import { w } from "./words";

type Finding = Schemas["FindingOut"];
const DECISIONS = ["relevant", "not_relevant", "explained_elsewhere"] as const;
const NEEDS_REASON = new Set<string>(["not_relevant", "explained_elsewhere"]);

/** The words for a finding: the check's own key if the interface has one, else the rule type's, softer when gentle. */
export function findingTitle(f: Finding): string {
  const rule = f.gentle
    ? t(`understand.rule.${f.rule}.gentle`, {}, w("rule.gentle_default"))
    : t(`understand.rule.${f.rule}`, {}, humanise(f.rule));
  return t(`understand.check.${f.check_key}${f.gentle ? ".gentle" : ""}`, {}, rule);
}

function FindingCard({ caseId, finding, onDecided }: { caseId: string; finding: Finding; onDecided: (f: Finding) => void }) {
  const [decision, setDecision] = useState<string | null>(finding.decision);
  const [reason, setReason] = useState(finding.reason ?? "");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const needs = decision !== null && NEEDS_REASON.has(decision);
  const ready = decision !== null && (!needs || reason.trim() !== "");
  const titleId = `finding-${finding.id}`;
  const save = async () => {
    setError(null);
    try {
      const out = await unwrap(
        api.PATCH("/v1/cases/{case_id}/findings/{finding_id}", {
          params: { path: { case_id: caseId, finding_id: finding.id } },
          body: { decision: decision as (typeof DECISIONS)[number], reason: needs ? reason.trim() : null },
        }),
      );
      setSaved(true);
      onDecided(out);
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <article aria-labelledby={titleId} className="av-card av-stack">
      <div>
        <h3 id={titleId}>{findingTitle(finding)}</h3>
        <p className="av-muted">
          {w("things.pages")}: <span>{finding.evidence_pages.map(pageLabel).join(", ")}</span>
        </p>
      </div>
      <fieldset className="av-understand-choice">
        <legend className="av-field__label">{w("things.decision")}</legend>
        <div className="av-understand-choice__options">
          {DECISIONS.map((d) => (
            <label key={d} className="av-checkbox av-understand-option">
              <input type="radio" name={`decision-${finding.id}`} value={d} checked={decision === d} onChange={() => { setDecision(d); setSaved(false); }} />
              <span>{t(`understand.things.decision.${d}`)}</span>
            </label>
          ))}
        </div>
      </fieldset>
      {needs && <TextArea label={w("things.reason")} hint={w("things.reason_hint")} rows={3} required value={reason} onChange={(e) => { setReason(e.target.value); setSaved(false); }} />}
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {saved && <p className="av-muted" role="status">{w("things.decided")}</p>}
      <div>
        <Button disabled={!ready} onClick={() => void save()}>{w("things.save")}</Button>
      </div>
    </article>
  );
}

function Checks({ caseId }: { caseId: string }) {
  const found = useResource(() => unwrap(api.GET("/v1/me/checks", { params: { query: { case_id: caseId } } })), [caseId]);
  const [enabled, setEnabled] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const checks = found.data && "checks" in found.data ? found.data.checks : [];
  useEffect(() => setEnabled(Object.fromEntries(checks.map((c) => [c.key, c.enabled]))), [found.data]); // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = async (key: string, on: boolean) => {
    setError(null);
    setEnabled((e) => ({ ...e, [key]: on }));
    try {
      await unwrap(api.PATCH("/v1/me/checks", { body: { check_key: key, enabled: on } }));
    } catch (err) {
      setEnabled((e) => ({ ...e, [key]: !on }));
      setError(errorMessage(err));
    }
  };
  return (
    <section aria-labelledby="things-checks" className="av-card av-stack">
      <div>
        <h2 id="things-checks">{w("things.checks")}</h2>
        <p className="av-muted">{w("things.checks_hint")}</p>
      </div>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {checks.map((c) => (
        <Checkbox key={c.key} label={t(`understand.check.${c.key}`, {}, humanise(c.key))} checked={enabled[c.key] ?? c.enabled} onChange={(e) => void toggle(c.key, e.target.checked)} />
      ))}
    </section>
  );
}

export function ThingsToLook() {
  const { caseId = "" } = useParams();
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/findings", casePath(caseId))), [caseId]);
  const [decided, setDecided] = useState<Record<string, Finding>>({});
  const [started, setStarted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setDecided({}), [found.data]);
  if (found.error) return <div className="av-page"><LoadError onRetry={found.reload} /></div>;
  if (!found.data) return <div className="av-page"><Loading /></div>;
  const findings = found.data.findings.map((f) => decided[f.id] ?? f);
  const done = findings.filter((f) => f.decision !== null).length;
  const rerun = async () => {
    setError(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/findings/rerun", casePath(caseId)));
      setStarted(true);
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <div className="av-page av-stack">
      <div>
        <h2>{w("things.title")}</h2>
        <p className="av-muted">{w("things.intro")}</p>
        {findings.length > 0 && <p><strong>{w("things.progress", { done, total: findings.length })}</strong></p>}
      </div>
      {findings.length === 0 && <p className="av-muted">{w("things.none")}</p>}
      {findings.map((f) => (
        <FindingCard key={f.id} caseId={caseId} finding={f} onDecided={(out) => setDecided((d) => ({ ...d, [out.id]: out }))} />
      ))}
      <div className="av-row">
        <Button variant="secondary" onClick={() => void rerun()}>{w("things.rerun")}</Button>
      </div>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {started && <Notice><p>{w("things.rerun_started")}</p></Notice>}
      <Checks caseId={caseId} />
    </div>
  );
}
