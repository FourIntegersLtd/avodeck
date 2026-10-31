// Screen 9b, Reading by transcript (ADR 0028): every entry in date order. The browser only reports how long
// each entry was on screen; the server decides when a page counts as read, and suggestions never show here.
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorMessage } from "../../foundation/lib/errors";
import { useStepUp } from "../../foundation/lib/stepUp";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, LinkButton, LoadError, Loading, Notice, ProgressBar } from "../../foundation/ui";
import { count } from "./data";
import { w } from "./words";

type Entry = Schemas["TranscriptEntryOut"];
const LIMIT = 50;
const POLL_MS = 3000;
// Seconds' worth of milliseconds an entry must stay in view before it is reported.
const REPORT_AFTER_MS = 400;

function EntryCard({ entry, caseId, onSeen }: { entry: Entry; caseId: string; onSeen: (id: string, ms: number) => void }) {
  const ref = useRef<HTMLElement>(null);
  const since = useRef<number | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const flush = () => {
      if (since.current !== null) {
        const ms = Date.now() - since.current;
        since.current = null;
        if (ms >= REPORT_AFTER_MS) onSeen(entry.id, ms);
      }
    };
    const observer = new IntersectionObserver(
      ([hit]) => {
        if (hit?.isIntersecting) since.current ??= Date.now();
        else flush();
      },
      { threshold: 0.6 },
    );
    observer.observe(el);
    return () => {
      flush();
      observer.disconnect();
    };
  }, [entry.id, onSeen]);
  const day = entry.event_date ? new Date(`${entry.event_date}T00:00:00`).toLocaleDateString(undefined, { dateStyle: "long" }) : w("transcript.undated");
  return (
    <article ref={ref} className={`rd-entry${entry.folded ? " rd-entry--folded" : ""}`} aria-label={w("transcript.entry_label", { page: entry.page_no })}>
      <h2 className="rd-entry__head">
        <span className="rd-entry__page">{w("read.page_ref", { page: entry.page_no })}</span> {day} · {entry.kind.replace(/_/g, " ")}
        {entry.results.map((r) => (
          <span key={r} className="rd-entry__chip">{r.replace(/_/g, " ")}</span>
        ))}
      </h2>
      {entry.lines.map((line, i) => (
        <p key={i} className="rd-entry__line">{line}</p>
      ))}
      <Link to={`/cases/${caseId}/read/${entry.page_no}`}>{w("transcript.see_page")}</Link>
    </article>
  );
}

export function TranscriptRead() {
  const { item, pack } = useCaseFrame();
  const caseId = item.id;
  const stepUp = useStepUp();
  const [entries, setEntries] = useState<Entry[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState<Schemas["TranscriptList"]["status"] | null>(null);
  const [failed, setFailed] = useState(false);
  const [read, setRead] = useState<number | null>(null);
  const [pagesTotal, setPagesTotal] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [preparingFile, setPreparingFile] = useState(false);

  const progress = useCallback(async () => {
    try {
      const p = await unwrap(api.GET("/v1/cases/{case_id}/progress", { params: { path: { case_id: caseId } } }));
      setRead(p.read);
      setPagesTotal(p.total);
    } catch {
      /* the count is a convenience */
    }
  }, [caseId]);

  const load = useCallback(
    async (offset: number) => {
      try {
        const got = await unwrap(api.GET("/v1/cases/{case_id}/transcript/entries", { params: { path: { case_id: caseId }, query: { offset, limit: LIMIT } } }));
        setFailed(false);
        setStatus(got.status);
        setTotal(got.total);
        setEntries((old) => (offset === 0 ? got.entries : [...old, ...got.entries]));
      } catch {
        setFailed(true);
      }
    },
    [caseId],
  );

  useEffect(() => {
    void load(0);
    void progress();
  }, [load, progress]);

  useEffect(() => {
    if (status !== "preparing") return;
    const timer = setTimeout(() => void load(0), POLL_MS);
    return () => clearTimeout(timer);
  }, [status, load, entries]);

  const seen = useCallback(
    (id: string, ms: number) => {
      void unwrap(api.POST("/v1/cases/{case_id}/transcript/seen", { params: { path: { case_id: caseId } }, body: { entry_id: id, dwell_ms: ms, picture_opened: false } }))
        .then((r) => (r.pages_read.length ? progress() : undefined))
        .catch(() => undefined);
    },
    [caseId, progress],
  );

  async function download() {
    setMessage(null);
    setPreparingFile(true);
    try {
      const started = await stepUp(() => unwrap(api.POST("/v1/cases/{case_id}/transcript/download", { params: { path: { case_id: caseId } } })));
      if (!started) return;
      const job_id = started.job_id;
      for (let tries = 0; tries < 60; tries += 1) {
        try {
          const blob = await unwrap(api.GET("/v1/cases/{case_id}/transcript/download/{job_id}", { params: { path: { case_id: caseId, job_id } }, parseAs: "blob" }));
          if (typeof URL.createObjectURL === "function") {
            const link = document.createElement("a");
            link.href = URL.createObjectURL(blob as Blob);
            link.download = `transcript-${caseId.slice(0, 8)}.docx`;
            link.click();
            URL.revokeObjectURL(link.href);
          }
          setMessage(w("transcript.downloaded"));
          return;
        } catch (err) {
          if ((err as { status?: number }).status !== 409) throw err;
          await new Promise((r) => setTimeout(r, POLL_MS));
        }
      }
      setMessage(w("transcript.download_slow"));
    } catch (err) {
      setMessage(errorMessage(err));
    } finally {
      setPreparingFile(false);
    }
  }

  if (failed && entries.length === 0) return <LoadError onRetry={() => void load(0)} />;
  if (status === null) return <Loading />;
  return (
    <div className="rd-transcript">
      <header className="rd-transcript__bar">
        <h1>{w("transcript.heading")}</h1>
        <LinkButton variant="secondary" to={`/cases/${caseId}/read`}>{w("transcript.page_by_page")}</LinkButton>
        <Button variant="secondary" onClick={() => void download()} disabled={preparingFile || status !== "ready"}>
          {preparingFile ? w("transcript.preparing_file") : w("transcript.download")}
        </Button>
        {read !== null && pagesTotal > 0 && (
          <div className="rd-transcript__progress">
            <p>{w("read.read_count", { read: count(read, pack.locale), total: count(pagesTotal, pack.locale) })}</p>
            <ProgressBar max={pagesTotal || 1} value={read} label={w("read.progress_done", { read, total: pagesTotal })} />
          </div>
        )}
      </header>
      {message && <Notice tone="info"><p>{message}</p></Notice>}
      <p>{w("transcript.lead")}</p>
      {status === "preparing" && <Notice tone="info"><p>{w("transcript.preparing")}</p></Notice>}
      {status === "failed" && (
        <Notice tone="warning">
          <p>{w("transcript.failed")}</p>
          <Button onClick={() => void load(0)}>{w("transcript.try_again")}</Button>
        </Notice>
      )}
      {status === "idle" && <Notice tone="info"><p>{w("transcript.preparing")}</p></Notice>}
      {status === "ready" && entries.length === 0 && <p>{w("transcript.none")}</p>}
      {entries.map((entry) => (
        <EntryCard key={entry.id} entry={entry} caseId={caseId} onSeen={seen} />
      ))}
      {entries.length < total && <Button variant="secondary" onClick={() => void load(entries.length)}>{w("transcript.more")}</Button>}
    </div>
  );
}
