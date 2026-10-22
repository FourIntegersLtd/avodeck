// S13 Things to look at. Code found these (the API's checks over checked facts); the expert decides each one.
// A reason is required for "not relevant" and "explained elsewhere". Nothing closes on its own: a decided finding
// a later check no longer finds stays listed and says so. Buttons follow what the person may do on the case.
import { useEffect, useState } from "react";
import { humanise, t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { screenPath } from "../../foundation/lib/screens";
import { Button, ChoicePills, DictationBox, Dialog, IconTile, LinkButton, LoadError, Loading, Notice, PageRef, TextField, ToggleSwitch } from "../../foundation/ui";
import { useParams } from "react-router-dom";
import { LinkReview } from "./LinkReview";
import { hasIcon, LineIcon } from "./laneIcons";
import { casePath, kindWord, longDate, shortDay, useCan } from "./shared";
import { w } from "./words";

type Finding = Schemas["FindingOut"];
const DECISIONS = ["relevant", "not_relevant", "explained_elsewhere"] as const;
const NEEDS_REASON = new Set<string>(["not_relevant", "explained_elsewhere"]);

/** The words for a finding: the check's own key if the interface has one, else the rule type's, softer when gentle. */
export function findingTitle(f: Finding): string {
  if (f.title) return f.title;
  const rule = f.gentle
    ? t(`understand.rule.${f.rule}.gentle`, {}, w("rule.gentle_default"))
    : t(`understand.rule.${f.rule}`, {}, humanise(f.rule));
  return t(`understand.check.${f.check_key}${f.gentle ? ".gentle" : ""}`, {}, rule);
}

function FindingCard({ caseId, finding, canDecide, onDecided }: { caseId: string; finding: Finding; canDecide: boolean; onDecided: (f: Finding) => void }) {
  const [decision, setDecision] = useState<string | null>(finding.decision);
  const [reason, setReason] = useState(finding.reason ?? "");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const needs = decision !== null && NEEDS_REASON.has(decision);
  const ready = decision !== null && (!needs || reason.trim() !== "");
  const titleId = `finding-${finding.id}`;
  const save = async (chosen: string, why: string | null) => {
    setError(null);
    try {
      const out = await unwrap(
        api.PATCH("/v1/cases/{case_id}/findings/{finding_id}", {
          params: { path: { case_id: caseId, finding_id: finding.id } },
          body: { decision: chosen as (typeof DECISIONS)[number], reason: why },
        }),
      );
      setSaved(true);
      onDecided(out);
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  const choose = (value: string) => {
    setDecision(value);
    setSaved(false);
    if (!NEEDS_REASON.has(value)) void save(value, null);
  };
  const shown = new Set(finding.evidence.map((e) => e.page_no));
  const other = finding.evidence_pages.filter((p) => !shown.has(p));
  return (
    <article aria-labelledby={titleId} className={`av-card av-stack av-understand-finding${finding.decision ? "" : " av-understand-finding--open"}`}>
      <div className="av-understand-finding__body">
        <div className="av-understand-finding__head">
          <IconTile tone="amber">
            <span data-testid="finding-icon" className="av-understand-finding__icon">
              {hasIcon(finding.icon) ? (
                <LineIcon icon={finding.icon} size={22} />
              ) : (
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
                  <path d="M5 21V4M5 4h11l-2 4 2 4H5" />
                </svg>
              )}
            </span>
          </IconTile>
          <h3 id={titleId}>{findingTitle(finding)}</h3>
        </div>
        <ul className="av-understand-list av-understand-finding__evidence">
          {finding.evidence.map((e) => (
            <li key={e.fact_id} className="av-understand-event">
              <span className="av-muted">{e.summary ? w("things.evidence", { date: longDate(e.event_date), summary: e.summary }) : `${kindWord(e.kind)} · ${longDate(e.event_date)}`}</span>
              <PageRef page={e.page_no} to={`/cases/${caseId}/read/${e.page_no}`} />
            </li>
          ))}
        </ul>
        {other.length > 0 && (
          <p className="av-muted">
            {w("things.pages")}: <span>{other.map((p) => w("common.page", { page: p })).join(", ")}</span>
          </p>
        )}
        {finding.status === "gone" && <p><strong>{w("things.gone")}</strong></p>}
      </div>
      {!canDecide ? (
        finding.decision && <p>{w("things.decision")}: {t(`understand.things.decision.${finding.decision}`)}</p>
      ) : (
        <>
          <ChoicePills
            label={w("things.decision")}
            hideLabel
            tone="amber"
            name={`decision-${finding.id}`}
            value={decision}
            onChange={choose}
            options={DECISIONS.map((d) => ({ value: d, label: t(`understand.things.decision.${d}`) }))}
          />
          {needs && (
            <>
              <p className="av-muted">{w("things.reason_hint")}</p>
              <DictationBox caseId={caseId} context="finding_reason" label={w("things.reason")} rows={2} bar value={reason} onChange={(v) => { setReason(v); setSaved(false); }} />
              <div>
                <Button disabled={!ready} onClick={() => void save(decision, reason.trim())}>{w("things.save")}</Button>
              </div>
            </>
          )}
          {error && <Notice tone="error"><p>{error}</p></Notice>}
          {saved && <p className="av-muted" role="status">{w("things.decided")}</p>}
        </>
      )}
    </article>
  );
}

/** A finding you have decided, folded into one line (title, date, your decision); open it to change the decision. */
function FoldedFinding({ caseId, finding, canDecide, onDecided }: { caseId: string; finding: Finding; canDecide: boolean; onDecided: (f: Finding) => void }) {
  const [open, setOpen] = useState(false);
  const day = finding.evidence.find((e) => e.event_date)?.event_date;
  const label = finding.status === "gone" ? w("things.gone_short") : t(`understand.things.decision.${finding.decision}`);
  return (
    <details className="av-card av-understand-folded" aria-label={findingTitle(finding)} open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>
        <span className="av-understand-key av-understand-key--checked av-understand-folded__tick" aria-hidden="true" />
        <span className="av-understand-folded__title">{day ? w("things.folded", { title: findingTitle(finding), date: shortDay(day) }) : findingTitle(finding)}</span>
        <span className="av-understand-count">{label}</span>
      </summary>
      {open && <FindingCard caseId={caseId} finding={finding} canDecide={canDecide} onDecided={onDecided} />}
    </details>
  );
}

function AddCheck({ caseId, onClose, onAdded }: { caseId: string; onClose: () => void; onAdded: () => void }) {
  const pack = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/pack", casePath(caseId))), [caseId]);
  const kinds = ((pack.data?.fact_kinds ?? []) as Array<{ key: string }>).map((k) => k.key);
  const [label, setLabel] = useState("");
  const [from, setFrom] = useState("");
  const [follow, setFollow] = useState("");
  const [days, setDays] = useState("14");
  const [error, setError] = useState<string | null>(null);
  const n = Number(days);
  const ready = label.trim() !== "" && from !== "" && follow !== "" && Number.isInteger(n) && n > 0;
  const add = async () => {
    setError(null);
    try {
      await unwrap(api.POST("/v1/me/checks/custom", { body: { case_id: caseId, label: label.trim(), applies_to: from, followup_kinds: [follow], window_days: n } }));
      onAdded();
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  const options = (
    <>
      <option value="">{w("things.own.choose")}</option>
      {kinds.map((k) => (
        <option key={k} value={k}>{kindWord(k)}</option>
      ))}
    </>
  );
  return (
    <Dialog title={w("things.own")} onClose={onClose}>
      <form className="av-stack" onSubmit={(e) => { e.preventDefault(); if (ready) void add(); }}>
        <p className="av-muted">{w("things.own_hint")}</p>
        <TextField name="own-label" label={w("things.own.label")} maxLength={80} value={label} onChange={(e) => setLabel(e.target.value)} />
        <div className="av-field">
          <label className="av-field__label" htmlFor="own-from">{w("things.own.from")}</label>
          <select id="own-from" className="av-input" value={from} onChange={(e) => setFrom(e.target.value)}>{options}</select>
        </div>
        <div className="av-field">
          <label className="av-field__label" htmlFor="own-follow">{w("things.own.follow")}</label>
          <select id="own-follow" className="av-input" value={follow} onChange={(e) => setFollow(e.target.value)}>{options}</select>
        </div>
        <TextField name="own-days" label={w("things.own.days")} type="number" min={1} inputMode="numeric" value={days} onChange={(e) => setDays(e.target.value)} />
        {error && <Notice tone="error"><p>{error}</p></Notice>}
        <div className="av-row">
          <Button type="submit" disabled={!ready}>{w("things.own.add")}</Button>
          <Button variant="secondary" onClick={onClose}>{w("common.cancel")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function Checks({ caseId, can }: { caseId: string; can: (a: string) => boolean }) {
  const [adding, setAdding] = useState(false);
  const found = useResource(() => unwrap(api.GET("/v1/me/checks", { params: { query: { case_id: caseId } } })), [caseId]);
  const [enabled, setEnabled] = useState<Record<string, boolean>>({});
  const [days, setDays] = useState<Record<string, string>>({});
  const [daysSaved, setDaysSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const checks = found.data && "checks" in found.data ? found.data.checks : [];
  useEffect(() => {
    setEnabled(Object.fromEntries(checks.map((c) => [c.key, c.enabled])));
    setDays(Object.fromEntries(checks.filter((c) => c.window_days != null).map((c) => [c.key, String(c.window_days)])));
  }, [found.data]); // eslint-disable-line react-hooks/exhaustive-deps
  const timed = checks.filter((c) => c.window_days != null);
  const saveDays = async () => {
    setError(null);
    setDaysSaved(false);
    try {
      for (const c of timed) {
        const n = Number(days[c.key]);
        if (Number.isInteger(n) && n > 0 && n !== c.window_days) await unwrap(api.PATCH("/v1/me/checks", { body: { check_key: c.key, window_days: n } }));
      }
      setDaysSaved(true);
    } catch (err) {
      setError(errorMessage(err));
    }
  };
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
    <section aria-labelledby="things-checks" className="av-card av-stack av-understand-checks">
      <div>
        <h2 id="things-checks">{w("things.checks")}</h2>
        <p className="av-muted">{w("things.checks_hint")}</p>
      </div>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {checks.map((c) => (
        <div key={c.key} className="av-understand-check">
          <ToggleSwitch labelFirst label={c.label ?? c.title ?? t(`understand.check.${c.key}`, {}, humanise(c.key))} checked={enabled[c.key] ?? c.enabled} onChange={(on) => void toggle(c.key, on)} />
          {c.gentle && <span className="av-muted">{w("things.gentle")}</span>}
        </div>
      ))}
      {can("finding.decide") && (
        <div>
          <Button variant="secondary" onClick={() => setAdding(true)}>{w("things.own")}</Button>
        </div>
      )}
      <p className="av-muted">{w("things.gentle_note")}</p>
      {adding && <AddCheck caseId={caseId} onClose={() => setAdding(false)} onAdded={() => { setAdding(false); found.reload(); }} />}
      {timed.length > 0 && (
        <details className="av-understand-days">
          <summary>{w("things.days_open")}</summary>
          <div className="av-stack">
            {timed.map((c) => (
              <TextField
                key={`days-${c.key}`}
                name={`days-${c.key}`}
                label={w("things.days", { check: c.label ?? c.title ?? t(`understand.check.${c.key}`, {}, humanise(c.key)) })}
                type="number"
                min={1}
                inputMode="numeric"
                value={days[c.key] ?? String(c.window_days)}
                onChange={(e) => { setDays((d) => ({ ...d, [c.key]: e.target.value })); setDaysSaved(false); }}
              />
            ))}
            <div>
              <Button variant="secondary" onClick={() => void saveDays()}>{w("things.days_save")}</Button>
            </div>
            {daysSaved && <p className="av-muted" role="status">{w("things.days_saved")}</p>}
          </div>
        </details>
      )}
    </section>
  );
}

export function ThingsToLook() {
  const { caseId = "" } = useParams();
  const can = useCan(caseId);
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
    <div className="av-page av-page--wide av-stack">
      <div className="av-understand-things">
        <div className="av-understand-things__main av-stack">
          <div>
            <h2>{w("things.title")}</h2>
            <p className="av-muted">{w("things.intro")}</p>
            {findings.length > 0 && <p><strong>{w("things.progress", { done, total: findings.length })}</strong></p>}
          </div>
          {findings.length === 0 && <p className="av-muted">{w("things.none")}</p>}
          {[...findings.filter((f) => f.decision === null), ...findings.filter((f) => f.decision !== null)].map((f) => {
            const Row = f.decision === null ? FindingCard : FoldedFinding;
            return <Row key={f.id} caseId={caseId} finding={f} canDecide={can("finding.decide")} onDecided={(out) => setDecided((d) => ({ ...d, [out.id]: out }))} />;
          })}
          {can("finding.decide") && (
            <div className="av-row">
              <Button variant="quiet" onClick={() => void rerun()}>{w("things.rerun")}</Button>
            </div>
          )}
          {error && <Notice tone="error"><p>{error}</p></Notice>}
          {started && <Notice><p>{w("things.rerun_started")}</p></Notice>}
          <details className="av-understand-links">
            <summary>{w("things.links_open")}</summary>
            <LinkReview caseId={caseId} can={can} />
          </details>
        </div>
        <div className="av-understand-things__side">
          <Checks caseId={caseId} can={can} />
        </div>
      </div>
      <div className="av-understand-foot av-understand-foot--end">
        <LinkButton to={screenPath("points_against", { caseId }) ?? screenPath("S14", { caseId }) ?? `/cases/${caseId}/points`} arrow>{w("things.start_writing")}</LinkButton>
      </div>
    </div>
  );
}
