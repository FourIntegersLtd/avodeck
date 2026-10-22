// Screen 07, Preparing your bundle. It follows the case's latest processing job (GET /process) over
// the job's event stream and opens screen 08 when the bundle is ready. The server decides everything;
// this screen only shows the job's step, progress and time left.
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorMessage } from "../../foundation/lib/errors";
import { screenPath } from "../../foundation/lib/screens";
import { useResource } from "../../foundation/lib/useResource";
import { api, apiOrigin, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, LoadError, Loading, Notice, ProgressBar, Spinner, TextLink } from "../../foundation/ui";
import { can, casePath } from "./data";
import { LockIcon, PagesPicture } from "./icons";
import { w, wn } from "./words";

const STEPS = ["split", "ocr", "sort", "duplicates", "missing"] as const;
const SCAN = "scan_document";
// The steps that handle every page, so a finished one shows the bundle's page count.
const COUNTED = new Set(["split", "ocr"]);
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
  const kept = info.data?.exhibits ?? [];
  const exhibits = kept.length;
  const audioOnly = exhibits > 0 && kept.every((e) => e.media_type.startsWith("audio/"));
  const pages = info.data?.pages ?? 0;
  const num = (n: number) => new Intl.NumberFormat(pack.locale).format(n);
  const running = job && !finished(job.status);
  const size = files ? wn("preparing.size", files, pack.locale, { pages: new Intl.NumberFormat(pack.locale).format(info.data?.pages ?? 0) }) : "";
  const left = running && job.eta_seconds != null ? wn("preparing.left", Math.max(1, Math.ceil(job.eta_seconds / 60)), pack.locale) : "";
  const current = job ? (job.status === "done" ? STEPS.length : STEPS.indexOf(job.step as (typeof STEPS)[number])) : -1;
  const questions = screenPath("questions", { caseId });

  const stopButton =
    running && uploads ? (
      <button type="button" className="av-text-link" onClick={stop} disabled={busy}>
        {w("preparing.stop")}
      </button>
    ) : null;

  return (
    <div className="av-page pl-preparing">
      <header className="pl-preparing__head">
        <PagesPicture label={w("preparing.picture")} />
        <h1>{w("preparing.title")}</h1>
        {(size || left) && <p className="pl-lead">{[size, left].filter(Boolean).join(" ")}</p>}
      </header>
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
                {state === "current" ? <Spinner /> : <StepMark state={state} />}
                <div className="pl-step__body">
                  <div className="pl-step__line">
                    <span className="pl-step__name">{w(`preparing.step.${step}`)}</span>
                    {state === "current" && job.step_total != null && job.step_total > 0 && (
                      <span className="av-mono pl-step__count">{w("preparing.count", { done: num(job.step_done ?? 0), total: num(job.step_total) })}</span>
                    )}
                    {state === "done" && pages > 0 && COUNTED.has(step) && <span className="av-mono pl-step__count">{num(pages)}</span>}
                  </div>
                  <span className="av-visually-hidden">{w(`preparing.state.${state}`)}</span>
                  {state === "current" && job.pct != null && <ProgressBar value={Math.max(0, Math.min(100, job.pct))} max={100} label={w("preparing.progress", { pct: Math.max(0, Math.min(100, job.pct)) })} />}
                </div>
              </li>
            );
          })}
        </ol>
      )}
      <div className="pl-notes">
        <p>
          <span className="pl-kept">
            <LockIcon />
            {w("preparing.kept")}
          </span>
        </p>
        <p>{w("preparing.close")}</p>
        {(exhibits > 0 || stopButton) && (
          <p>
            {exhibits > 0 && wn(audioOnly ? "preparing.exhibits_audio" : "preparing.exhibits", exhibits, pack.locale)} {stopButton}
          </p>
        )}
        {questions && (
          <p>
            <TextLink to={questions}>{w("preparing.meanwhile")}</TextLink>
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
      {state === "done" && <path d="M5 12l5 5 9-10" transform="translate(8 8) scale(0.75)" />}
    </svg>
  );
}
