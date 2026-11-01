// Add more papers: choose new files, say what they are and how and when they arrived, upload them
// as one new delivery (POST /documents/append), then follow the processing job. The server numbers
// the new pages after the existing ones; this screen only sends files and shows progress.
import { useEffect, useRef, useState } from "react";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { useResource } from "../../foundation/lib/useResource";
import { errorMessage } from "../../foundation/lib/errors";
import { screenPath } from "../../foundation/lib/screens";
import { uploadInParts } from "../../foundation/lib/upload";
import { ResumeUpload } from "../../foundation/lib/ResumeUpload";
import { UploadStatus } from "../../foundation/lib/UploadControls";
import { useJobWatch } from "../../foundation/lib/jobWatch";
import { useUpload } from "../../foundation/lib/useUpload";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, ChoicePills, DropZone, LinkButton, Notice, ProgressBar, Spinner, TextField } from "../../foundation/ui";
import { can, casePath, kindWord } from "./data";
import { w, wordFor } from "./words";

const POLL_MS = 2000;
const HOW = ["sent", "asked", "mine"] as const;
const BASE_KINDS = ["records", "other"];

type Job = Schemas["ProcessingJob"];
const today = () => new Date().toISOString().slice(0, 10);

export function AddPapers() {
  const { item, pack } = useCaseFrame();
  const caseId = item.id;
  const [files, setFiles] = useState<File[]>([]);
  const [kind, setKind] = useState("records");
  const [how, setHow] = useState<string>("sent");
  const [when, setWhen] = useState(today());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<{ id: string; latest: Partial<Job> } | null>(null);
  const saved = useRef(new Map<File, string>());
  const [recovered, setRecovered] = useState<string[]>([]);
  const upload = useUpload();
  const existing = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/bundle/summary", casePath(caseId))), [caseId]);
  const first = !!existing.data && existing.data.documents.length === 0;
  const { watch } = useJobWatch();

  const jobId = job?.id;
  const status = job?.latest.status;
  useEffect(() => {
    if (!jobId || status === "done" || status === "failed" || status === "cancelled") return;
    let live = true;
    const look = () =>
      unwrap(api.GET("/v1/jobs/{job_id}", { params: { path: { job_id: jobId } } })).then(
        (latest) => live && setJob({ id: jobId, latest }),
        () => undefined,
      );
    void look();
    const timer = window.setInterval(look, POLL_MS);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [jobId, status]);

  const extra = Object.keys(((pack.vocabulary as Record<string, unknown>).document_kinds as Record<string, string> | undefined) ?? {});
  const kinds = [...BASE_KINDS, ...extra.filter((k) => !BASE_KINDS.includes(k))].map((k) => ({ value: k, label: kindWord(pack, k) }));
  const hows = HOW.map((k) => ({ value: k, label: w(`add.how.${k}`) }));
  const home = screenPath("case_home", { caseId });

  if (!can(item, "case.upload")) {
    return (
      <div className="av-page pl-add">
        <h1>{w("add.title")}</h1>
        <Notice><p>{w("add.not_allowed")}</p></Notice>
        {home && <LinkButton to={home} variant="secondary">{w("add.back")}</LinkButton>}
      </div>
    );
  }

  async function send() {
    setBusy(true);
    setError(null);
    upload.begin();
    const failed: string[] = [];
    for (const file of files) {
      if (saved.current.has(file)) continue;
      try {
        saved.current.set(file, await uploadInParts(caseId, file, kind, true, upload.options()));
      } catch {
        // Stopped on purpose: back to ready with nothing said; what was already saved stays saved.
        if (upload.stopped()) {
          setBusy(false);
          return;
        }
        failed.push(file.name);
      }
    }
    if (failed.length) {
      setError(w("add.failed", { files: failed.join(", ") }));
      setBusy(false);
      return;
    }
    try {
      const ids = [...recovered, ...files.map((f) => saved.current.get(f)!)];
      for (const id of ids) {
        await unwrap(api.PATCH("/v1/cases/{case_id}/documents/{document_id}", { params: { path: { case_id: caseId, document_id: id } }, body: { obtained_how: w(`add.how.${how as (typeof HOW)[number]}`), received_on: when || null } }));
      }
      const { job_id } = await unwrap(api.POST("/v1/cases/{case_id}/documents/append", { ...casePath(caseId), body: { document_ids: ids } }));
      setJob({ id: job_id, latest: { status: "queued", pct: 0 } });
      watch(job_id, { done: w("add.done"), failed: w("add.toast_failed"), open: w("add.toast_open"), to: `/cases/${caseId}/bundle` });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  if (job) {
    const l = job.latest;
    const pct = Math.max(0, Math.min(100, l.pct ?? 0));
    return (
      <div className="av-page pl-add">
        <h1>{status === "done" ? w("add.done") : w("add.progress_title")}</h1>
        {status === "failed" && <Notice tone="error"><p>{w("add.job_failed")}</p></Notice>}
        {status !== "done" && status !== "failed" && (
          <div className="av-card pl-add__bar">
            <Spinner />
            <span>{l.step ? wordFor("preparing.step", l.step, w("add.working")) : w("add.working")}</span>
            <ProgressBar value={pct} max={100} label={w("add.progress", { pct })} />
            <p>{w("add.close")}</p>
          </div>
        )}
        {home && <LinkButton to={home} variant={status === "done" ? "primary" : "secondary"}>{w("add.back")}</LinkButton>}
      </div>
    );
  }

  return (
    <div className="av-page pl-add">
      <header>
        <h1>{w(first ? "add.title_first" : "add.title")}</h1>
        <p className="pl-lead">{w(first ? "add.lead_first" : "add.lead")}</p>
      </header>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      <ResumeUpload caseId={caseId} append onDocument={(id) => setRecovered((prev) => (prev.includes(id) ? prev : [...prev, id]))} />
      <DropZone title={w("add.drop")} hint={w("add.drop_hint")} multiple disabled={busy} added={files.length > 0} onFiles={(more) => setFiles((prev) => [...prev, ...more])}>
        {files.length > 0 && (
          <ul className="pl-add__files" aria-label={w("add.files")}>
            {files.map((f, i) => (
              <li key={`${f.name}-${i}`} className="pl-add__file">
                <span>{f.name}</span>
                <Button variant="secondary" disabled={busy} aria-label={w("add.remove", { name: f.name })} onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}>×</Button>
              </li>
            ))}
          </ul>
        )}
      </DropZone>
      <ChoicePills label={w("add.kind")} name="kind" value={kind} onChange={setKind} options={kinds} />
      <ChoicePills label={w("add.how")} name="how" value={how} onChange={setHow} options={hows} />
      <TextField label={w("add.when")} name="received_on" type="date" value={when} max={today()} onChange={(e) => setWhen(e.currentTarget.value)} />
      <div className="pl-actions">
        <UploadStatus upload={upload} withStatus={false} />
        <Button onClick={send} disabled={files.length === 0 && recovered.length === 0} busy={busy}>{busy ? (upload.status ?? w("add.sending")) : w("add.go")}</Button>
      </div>
    </div>
  );
}
