// S17 Final check (docs/design/screens/S17-FinalCheck.dc.html): what failed, with a way to fix it; what passed;
// tone and expertise warnings as places in the report (never edits); and the colleague spot-check.
import { useId, useState } from "react";
import { useParams } from "react-router-dom";
import { humanise } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { screenPath } from "../../foundation/lib/screens";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, LinkButton, LoadError, Loading, Notice, TextField } from "../../foundation/ui";
import { longDate, useLocale, useSectionName, useWaiting } from "./shared";
import { w } from "./words";

type Check = Schemas["FinalCheckOut"];
type Result = Schemas["CheckResult"];
const WARNING_TYPES = ["uncited_fact", "loaded_language", "outside_expertise"] as const;
const SHOWN = 7;
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

export function FinalCheck() {
  const { caseId = "" } = useParams();
  const found = useResource(() => latest(caseId), [caseId]);
  const wait = useWaiting(found.reload, found.data?.id ?? null);
  const [error, setError] = useState<string | null>(null);
  const [all, setAll] = useState(false);

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
    <div className="av-page av-stack">
      <h1>{title}</h1>
      <p>{w("check.intro")}</p>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {wait.waiting && <Notice><p>{w("check.running")}</p></Notice>}
      {!check && !wait.waiting && <Notice><p>{w("check.none")}</p></Notice>}
      {check && !check.current && <Notice tone="warning"><p>{w("check.out_of_date")}</p></Notice>}
      {check?.declaration_changed && <Notice tone="warning"><p>{w("check.declaration_changed")}</p></Notice>}

      {failed.length > 0 && (
        <ul className="av-report-list">
          {failed.map((r) => (
            <Failed key={r.key} caseId={caseId} result={r} />
          ))}
        </ul>
      )}
      {notRun.length > 0 && (
        <ul className="av-report-list">
          {notRun.map((r) => (
            <li key={r.key} className="av-card">
              <p><strong>{nameOf(r)}</strong></p>
              <p className="av-muted">{w("check.not_run")}</p>
            </li>
          ))}
        </ul>
      )}
      {check && passed.length > 0 && (
        <section className="av-card av-stack" aria-labelledby="passed-title">
          <h3 id="passed-title">{w("check.passed")}</h3>
          <p>{w("check.passed_count", { passed: passed.length, total: check.results.length })}</p>
          <ul className="av-report-list">
            {(all ? passed : passed.slice(0, SHOWN)).map((r) => (
              <li key={r.key} aria-label={nameOf(r)} className="av-report-item av-report-item--spread">
                <span><span aria-hidden="true">✓</span> {nameOf(r)}</span>
                {r.count > 0 && <span className="av-report-mono">{r.count}</span>}
              </li>
            ))}
          </ul>
          {!all && passed.length > SHOWN && <Button variant="quiet" onClick={() => setAll(true)}>{w("check.see_all", { count: passed.length })}</Button>}
        </section>
      )}
      {check && check.warnings.length > 0 && <Warnings warnings={check.warnings} />}
      {check?.warnings_status === "pending" && <p className="av-muted">{w("check.warnings_pending")}</p>}

      <SpotCheck caseId={caseId} />

      <div className="av-report-footer">
        <a href="#spot-check">{w("check.spot")}</a>
        <Button variant="secondary" onClick={() => void run()} disabled={wait.waiting}>
          {check ? w("check.run_again") : w("check.run")}
        </Button>
        {ready ? (
          <LinkButton to={`/cases/${caseId}/sign`}>{w("check.sign")}</LinkButton>
        ) : (
          check && failed.length > 0 && <Button disabled>{w("check.fix_first", { count: failed.length })}</Button>
        )}
      </div>
    </div>
  );
}

function Failed({ caseId, result }: { caseId: string; result: Result }) {
  const id = useId();
  const fix = result.fixes[0] ?? result.fix;
  const to = fix ? (screenPath(fix.screen, { caseId, params: { item: fix.item } }) ?? `/cases/${caseId}`) : null;
  return (
    <li className="av-card av-report-failed" aria-labelledby={id}>
      <p id={id}><strong>{nameOf(result)}</strong></p>
      {result.count > 0 && <p className="av-muted">{w("check.count", { count: result.count })}</p>}
      {to && <LinkButton variant="secondary" to={to}>{w("check.fix")}</LinkButton>}
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
          <p className="av-report-mono">{w("check.spot_link", { path: `/spot-check/${started.token}` })}</p>
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
