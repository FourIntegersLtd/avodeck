// Screen 07, Preparing your bundle. It follows the case's latest processing job (GET /process) over
// the job's event stream and opens screen 08 when the bundle is ready. The server decides everything;
// this screen only shows the job's step, progress and time left.
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorMessage } from "../../foundation/lib/errors";
import { screenPath } from "../../foundation/lib/screens";
import { useResource } from "../../foundation/lib/useResource";
import { api, apiOrigin, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, LoadError, Loading, Notice } from "../../foundation/ui";
import { can, casePath } from "./data";
import { w, wn } from "./words";

const STEPS = ["split", "ocr", "sort", "duplicates", "missing"] as const;
const SCAN = "scan_document";
const POLL_MS = 3000;

type Job = Schemas["ProcessingJob"];
type Update = Partial<Pick<Job, "status" | "step" | "pct" | "eta_seconds" | "error_code">>;

const finished = (status: string | undefined) => status === "done" || status === "failed" || status === "cancelled";

export function Preparing() {
  const { item, pack } = useCaseFrame();
  const caseId = item.id;
  const navigate = useNavigate();
  const info = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/bundle/summary", casePath(caseId))), [caseId]);
  const [job, setJob] = useState<Job | null | undefined>(undefined);
  const [loadFailed, setLoadFailed] = useState(false);
  const [stopped, setStopped] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const following = useRef<{ close: () => void } | null>(null);
  const uploads = can(item, "case.upload");

  const stopFollowing = () => {
    following.current?.close();
    following.current = null;
  };

  const check = useCallback(async () => {
    const { job: latest } = await unwrap(api.GET("/v1/cases/{case_id}/process", casePath(caseId)));
    if (latest && latest.status === "done" && latest.kind !== SCAN) {
      navigate(`/cases/${caseId}/bundle`, { replace: true });
    } else if (!latest || latest.status === "done" || latest.status === "cancelled") {
      setJob(null);
    } else if (latest.status === "failed") {
      setJob(latest);
    } else {
      follow(latest);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId, navigate]);

  const onUpdate = (update: Update) => {
    setJob((prev) => (prev ? { ...prev, ...update } : prev));
    if (finished(update.status)) {
      stopFollowing();
      if (update.status === "done") void check().catch(() => setLoadFailed(true));
    }
  };

  function follow(next: Job) {
    stopFollowing();
    setJob(next);
    if (typeof EventSource !== "undefined") {
      const events = new EventSource(`${apiOrigin()}/v1/jobs/${next.id}/events`, { withCredentials: true });
      events.onmessage = (e: MessageEvent<string>) => onUpdate(JSON.parse(e.data) as Update);
      following.current = events;
    } else {
      const timer = window.setInterval(() => {
        unwrap(api.GET("/v1/jobs/{job_id}", { params: { path: { job_id: next.id } } })).then(onUpdate, () => undefined);
      }, POLL_MS);
      following.current = { close: () => window.clearInterval(timer) };
    }
  }

  const load = useCallback(() => {
    setLoadFailed(false);
    check().catch(() => setLoadFailed(true));
  }, [check]);

  useEffect(() => {
    load();
    return stopFollowing;
  }, [load]);

  async function start() {
    setBusy(true);
    setActionError(null);
    try {
      const { job_id } = await unwrap(api.POST("/v1/cases/{case_id}/process", casePath(caseId)));
      setStopped(false);
      follow({ id: job_id, kind: "process_bundle", status: "queued", step: null, pct: 0, eta_seconds: null, error_code: null });
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    setBusy(true);
    setActionError(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/process/cancel", casePath(caseId)));
      stopFollowing();
      setStopped(true);
      setJob(null);
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  if (loadFailed) return <div className="av-page"><LoadError onRetry={load} /></div>;
  if (job === undefined) return <div className="av-page"><Loading /></div>;

  const files = info.data?.documents.length ?? 0;
  const exhibits = info.data?.exhibits?.length ?? 0;
  const running = job && !finished(job.status);
  const size = files ? wn("preparing.size", files, pack.locale, { pages: new Intl.NumberFormat(pack.locale).format(info.data?.pages ?? 0) }) : "";
  const left = running && job.eta_seconds != null ? wn("preparing.left", Math.max(1, Math.ceil(job.eta_seconds / 60)), pack.locale) : "";
  const current = job ? (job.status === "done" ? STEPS.length : STEPS.indexOf(job.step as (typeof STEPS)[number])) : -1;
  const questions = screenPath("questions", { caseId });

  return (
    <div className="av-page pl-preparing">
      <h1>{w("preparing.title")}</h1>
      {(size || left) && <p className="pl-lead">{[size, left].filter(Boolean).join(" ")}</p>}
      {actionError && <Notice tone="error"><p>{actionError}</p></Notice>}
      {stopped && <Notice><p>{w("preparing.stopped")}</p></Notice>}
      {job === null && !stopped && <Notice><p>{w("preparing.nothing")}</p></Notice>}
      {job?.status === "failed" && (
        <Notice tone="error">
          <p>{w("preparing.failed")}</p>
          {uploads && <Button variant="secondary" onClick={start} disabled={busy}>{w("preparing.again")}</Button>}
        </Notice>
      )}
      {job === null && uploads && (
        <div className="pl-actions">
          <Button onClick={start} disabled={busy}>{w("preparing.start")}</Button>
        </div>
      )}
      {job && (
        <ol className="av-card pl-steps" aria-label={w("preparing.steps")}>
          {STEPS.map((step, i) => {
            const state = i < current ? "done" : i === current ? "current" : "waiting";
            return (
              <li key={step} className={`pl-step pl-step--${state}`}>
                <StepMark state={state} />
                <div className="pl-step__body">
                  <span className="pl-step__name">{w(`preparing.step.${step}`)}</span>
                  <span className="av-visually-hidden">{w(`preparing.state.${state}`)}</span>
                  {state === "current" && job.pct != null && <Progress pct={job.pct} />}
                </div>
              </li>
            );
          })}
        </ol>
      )}
      <div className="pl-notes">
        <p>{w("preparing.kept")}</p>
        <p>{w("preparing.close")}</p>
        {exhibits > 0 && <p>{wn("preparing.exhibits", exhibits, pack.locale)}</p>}
        {running && uploads && (
          <Button variant="quiet" onClick={stop} disabled={busy}>
            {w("preparing.stop")}
          </Button>
        )}
        {questions && (
          <p>
            <Link to={questions}>{w("preparing.meanwhile")}</Link>
          </p>
        )}
      </div>
    </div>
  );
}

function StepMark({ state }: { state: "done" | "current" | "waiting" }) {
  return (
    <svg className={`pl-step__mark pl-step__mark--${state}`} width="34" height="34" viewBox="0 0 34 34" aria-hidden="true" focusable="false">
      <circle cx="17" cy="17" r="15" />
      {state === "done" && <path d="M11 17.5l4 4 8-8" />}
    </svg>
  );
}

function Progress({ pct }: { pct: number }) {
  const value = Math.max(0, Math.min(100, pct));
  return (
    <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={value} aria-label={w("preparing.progress", { pct: value })} className="pl-progress">
      <svg width="100%" height="8" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true" focusable="false">
        <rect className="pl-progress__track" x="0" y="0" width="100" height="8" rx="4" />
        <rect className="pl-progress__bar" x="0" y="0" width={value} height="8" rx="4" />
      </svg>
    </div>
  );
}
