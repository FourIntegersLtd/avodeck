// S17 Final check (docs/design/screens/S17-FinalCheck.dc.html): the score ring and what failed, with a way to fix
// it; what passed with its counts; tone and expertise warnings as places in the report (never edits); and the
// colleague spot-check behind one link.
import { useState } from "react";
import { useParams } from "react-router-dom";
import { humanise } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { screenPath } from "../../foundation/lib/screens";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Callout, LinkButton, LoadError, Loading, Notice, ProgressRing, TextField } from "../../foundation/ui";
import { longDate, useLocale, useSectionName, useWaiting } from "./shared";
import { w } from "./words";

type Check = Schemas["FinalCheckOut"];
type Result = Schemas["CheckResult"];
const WARNING_TYPES = ["uncited_fact", "loaded_language", "outside_expertise"] as const;
const SHOWN = 8;
const ERRORS = { not_built: "report.check.no_build" };
const nameOf = (r: Result) => r.text ?? humanise(r.key);

async function latest(caseId: string): Promise<Check | null> {
  try {
    return await unwrap(api.GET("/v1/cases/{case_id}/report/final-check/latest", { params: { path: { case_id: caseId } } }));
  } catch (err) {
    if (err instanceof ApiError && err.code === "not_found") return null;
    throw err;
  }
}

function AlertIcon() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true" focusable="false">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.5v5.5M12 16.5v.01" />
    </svg>
  );
}

export function FinalCheck() {
  const { caseId = "" } = useParams();
  const found = useResource(() => latest(caseId), [caseId]);
  const wait = useWaiting(found.reload, found.data?.id ?? null);
  const [error, setError] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const [spotOpen, setSpotOpen] = useState(false);

  const run = async () => {
    setError(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/report/final-check", { params: { path: { case_id: caseId } } }));
      wait.start();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };

  if (found.error) return <div className="av-page"><LoadError onRetry={found.reload} /></div>;
  if (found.loading && found.data === undefined) return <div className="av-page"><Loading /></div>;
  const check = found.data ?? null;
  const failed = check?.results.filter((r) => r.outcome === "failed") ?? [];
  const notRun = check?.results.filter((r) => r.outcome === "not_run") ?? [];
  const passed = check?.results.filter((r) => r.outcome === "passed") ?? [];
  const ready = !!check && check.passed && check.current;
  const title = failed.length > 0 ? w("check.title_fix", { count: failed.length }) : ready ? w("check.title_ok") : w("check.title");

  return (
    <div className="av-page rp-page rp-page--narrow">
      <div className="rp-hero">
        {check && (
          <ProgressRing size={116} value={passed.length} max={check.results.length} label={w("check.ring", { passed: passed.length, total: check.results.length })}>
            <span className="rp-score">
              <strong>{passed.length}</strong>
              <span>{w("check.ring_of", { total: check.results.length })}</span>
            </span>
          </ProgressRing>
        )}
        <div>
          <h1 className="rp-title">{title}</h1>
          <p className="rp-lead">{w("check.intro")}</p>
        </div>
      </div>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {wait.waiting && <Notice><p>{w("check.running")}</p></Notice>}
      {!check && !wait.waiting && <Notice><p>{w("check.none")}</p></Notice>}
      {check && !check.current && <Notice tone="warning"><p>{w("check.out_of_date")}</p></Notice>}
      {check?.declaration_changed && <Notice tone="warning"><p>{w("check.declaration_changed")}</p></Notice>}

      {(failed.length > 0 || notRun.length > 0) && (
        <ul className="rp-fixes">
          {failed.map((r) => (
            <Failed key={r.key} caseId={caseId} result={r} />
          ))}
          {notRun.map((r) => (
            <li key={r.key} aria-label={nameOf(r)}>
              <Callout title={nameOf(r)} icon={<AlertIcon />}>
                <p>{w("check.not_run")}</p>
              </Callout>
            </li>
          ))}
        </ul>
      )}
      {check && passed.length > 0 && (
        <ul className="rp-passed" aria-label={w("check.passed")}>
          {(all ? passed : passed.slice(0, SHOWN)).map((r, i, shown) => (
            <li key={r.key} aria-label={nameOf(r)} className="rp-passed__row">
              <svg className="rp-tick" width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M5 12.5l4.5 4.5L19 7.5" />
              </svg>
              <span className="rp-passed__name">{nameOf(r)}</span>
              {r.count > 0 && <span className="av-mono rp-passed__count">{r.count}</span>}
              {!all && passed.length > SHOWN && i === shown.length - 1 && (
                <button type="button" className="av-text-link rp-linkbtn" onClick={() => setAll(true)}>{w("check.see_all", { count: passed.length })}</button>
              )}
            </li>
          ))}
        </ul>
      )}
      {check && check.warnings.length > 0 && <Warnings warnings={check.warnings} />}
      {check?.warnings_status === "pending" && <p className="av-muted">{w("check.warnings_pending")}</p>}

      {spotOpen && <SpotCheck caseId={caseId} />}

      <div className="rp-bar">
        <div className="rp-bar__actions">
          <button type="button" className="av-text-link rp-linkbtn" aria-expanded={spotOpen} onClick={() => setSpotOpen(!spotOpen)}>{w("check.spot")}</button>
          {check && <button type="button" className="av-text-link rp-linkbtn" onClick={() => void run()} disabled={wait.waiting}>{w("check.run_again")}</button>}
        </div>
        <div className="rp-bar__actions">
          {!check ? (
            <Button onClick={() => void run()} disabled={wait.waiting}>{w("check.run")}</Button>
          ) : ready ? (
            <LinkButton arrow to={`/cases/${caseId}/sign`}>{w("check.sign")}</LinkButton>
          ) : (
            <>
              {failed.length > 0 && <span className="av-muted">{w("check.fix_first", { count: failed.length })}</span>}
              <Button disabled>{w("check.sign")}</Button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Failed({ caseId, result }: { caseId: string; result: Result }) {
  const fix = result.fixes[0] ?? result.fix;
  const to = fix ? (screenPath(fix.screen, { caseId, params: { item: fix.item } }) ?? `/cases/${caseId}`) : null;
  return (
    <li aria-label={nameOf(result)}>
      <Callout title={nameOf(result)} icon={<AlertIcon />} actions={to ? <LinkButton to={to}>{w("check.fix")}</LinkButton> : undefined}>
        {result.count > 1 && <p>{w("check.count", { count: result.count })}</p>}
      </Callout>
    </li>
  );
}

function Warnings({ warnings }: { warnings: Schemas["Warning"][] }) {
  const nameOf = useSectionName();
  return (
    <section className="av-card av-stack" aria-labelledby="warn-title">
      <h3 id="warn-title">{w("check.warnings")}</h3>
      <p className="av-muted">{w("check.warnings_intro")}</p>
      <ul className="av-report-list">
        {warnings.map((warning) => {
          const known = (WARNING_TYPES as readonly string[]).includes(warning.type);
          return (
            <li key={`${warning.paragraph_id}-${warning.type}-${warning.words}`} className="av-report-item">
              <p><strong>{known ? w(`check.warning.${warning.type as (typeof WARNING_TYPES)[number]}`) : humanise(warning.type)}</strong></p>
              <p className="av-muted">{w("check.warning.where", { section: nameOf(warning.section), para: warning.para_no })}</p>
              <p className="av-report-words">{warning.words}</p>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function SpotCheck({ caseId }: { caseId: string }) {
  const locale = useLocale();
  const list = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/spot-checks", { params: { path: { case_id: caseId } } })), [caseId]);
  const [email, setEmail] = useState("");
  const [started, setStarted] = useState<Schemas["SpotCheckStarted"] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const send = async () => {
    setError(null);
    try {
      setStarted(await unwrap(api.POST("/v1/cases/{case_id}/spot-checks", { params: { path: { case_id: caseId } }, body: { reviewer_email: email } })));
      setEmail("");
      list.reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <section id="spot-check" className="av-card av-stack" aria-labelledby="spot-title">
      <h3 id="spot-title">{w("check.spot")}</h3>
      <p>{w("check.spot_intro")}</p>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {started && (
        <Notice tone="success">
          <p>{w("check.spot_sent")}</p>
          <p className="av-report-mono">{w("check.spot_link", { path: `${window.location.origin}/spot-check/${started.token}` })}</p>
          <p>{w("check.spot_once", { date: longDate(started.expires_at, locale) })}</p>
        </Notice>
      )}
      <TextField label={w("check.spot_email")} name="reviewer_email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      <div className="av-row">
        <Button variant="secondary" disabled={!email.includes("@")} onClick={() => void send()}>{w("check.spot_send")}</Button>
      </div>
      {(list.data?.spot_checks.length ?? 0) > 0 && (
        <>
          <h4>{w("check.spot_list")}</h4>
          <ul className="av-report-list">
            {list.data!.spot_checks.map((s) => (
              <li key={s.id} className="av-report-item">
                <SpotRow spot={s} />
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function SpotRow({ spot }: { spot: Schemas["SpotCheckOut"] }) {
  if (spot.locked) return <p>{w("check.spot_locked", { email: spot.reviewer_email })}</p>;
  if (spot.responses.length === 0) return <p>{w("check.spot_waiting", { email: spot.reviewer_email })}</p>;
  const agree = spot.responses.filter((r) => r.agrees).length;
  return (
    <>
      <p>{w("check.spot_answered", { email: spot.reviewer_email, agree, total: spot.responses.length })}</p>
      {spot.responses.filter((r) => !r.agrees && r.reason).map((r) => (
        <p key={r.fact_id} className="av-muted">{w("check.spot_disagree", { reason: r.reason })}</p>
      ))}
    </>
  );
}
