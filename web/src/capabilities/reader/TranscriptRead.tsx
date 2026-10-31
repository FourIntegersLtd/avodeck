// Screen 9b, Reading by transcript (ADR 0028): every entry in date order. The browser only reports how long
// each entry was on screen; the server decides when a page counts as read, and suggestions never show here.
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorMessage } from "../../foundation/lib/errors";
import { humanise, t } from "../../foundation/i18n";
import { useStepUp } from "../../foundation/lib/stepUp";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, LinkButton, LoadError, Loading, Notice, ProgressBar } from "../../foundation/ui";
import { count } from "./data";
import { smallPageImage } from "./pageImageCache";
import { w } from "./words";

type Entry = Schemas["TranscriptEntryOut"];
const LIMIT = 50;
const POLL_MS = 3000;
// Seconds' worth of milliseconds an entry must stay in view before it is reported.
const REPORT_AFTER_MS = 400;

type SeenFn = (view: { entry_id?: string; page_no?: number; dwell_ms?: number; picture_opened: boolean }) => void;

// An entry counts as on screen when most of it is in view, or when it fills most of the screen (a tall entry can
// never be 60 per cent in view). The server still decides when a page is read.
const STEPS = Array.from({ length: 21 }, (_, i) => i / 20);
const MOST = 0.6;

function onScreen(hit: IntersectionObserverEntry): boolean {
  if (!hit.isIntersecting) return false;
  if (hit.intersectionRatio >= MOST) return true;
  const screenHeight = hit.rootBounds?.height || window.innerHeight;
  return screenHeight > 0 && hit.intersectionRect.height >= MOST * screenHeight;
}

/** The small picture of a page, opened by the reader; opening it is reported (the way a page with no entries is read). */
function PagePicture({ caseId, pageNo, onSeen }: { caseId: string; pageNo: number; onSeen: SeenFn }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  async function open() {
    try {
      setSrc(await smallPageImage(caseId, pageNo));
      setFailed(false);
      onSeen({ page_no: pageNo, picture_opened: true });
    } catch {
      setFailed(true);
    }
  }
  if (src) return <img className="rd-entry__picture" src={src} alt={w("transcript.picture_alt", { page: pageNo })} />;
  return (
    <>
      <Button variant="secondary" onClick={() => void open()}>{w("transcript.open_picture", { page: pageNo })}</Button>
      {failed && <p role="alert">{w("transcript.picture_failed")}</p>}
    </>
  );
}

function EntryCard({ entry, caseId, onSeen }: { entry: Entry; caseId: string; onSeen: SeenFn }) {
  const ref = useRef<HTMLElement>(null);
  const since = useRef<number | null>(null);
  // An administrative entry is folded; it counts as seen only once the reader has unfolded it.
  const [open, setOpen] = useState(!entry.folded);
  useEffect(() => {
    const el = ref.current;
    if (!el || !open || typeof IntersectionObserver === "undefined") return;
    const flush = () => {
      if (since.current !== null) {
        const ms = Date.now() - since.current;
        since.current = null;
        if (ms >= REPORT_AFTER_MS) onSeen({ entry_id: entry.id, dwell_ms: ms, picture_opened: false });
      }
    };
    const observer = new IntersectionObserver(
      ([hit]) => {
        if (hit && onScreen(hit)) since.current ??= Date.now();
        else flush();
      },
      { threshold: STEPS },
    );
    observer.observe(el);
    return () => {
      flush();
      observer.disconnect();
    };
  }, [entry.id, onSeen, open]);
  const day = entry.event_date ? new Date(`${entry.event_date}T00:00:00`).toLocaleDateString(undefined, { dateStyle: "long" }) : w("transcript.undated");
  return (
    <article ref={ref} className={`rd-entry${entry.folded ? " rd-entry--folded" : ""}`} aria-label={w("transcript.entry_label", { page: entry.page_no })}>
      <h2 className="rd-entry__head">
        <span className="rd-entry__page">{w("read.page_ref", { page: entry.page_no })}</span> {day} · {t(entry.kind_label, {}, humanise(entry.kind))}
        {entry.results.map((key, i) => (
          <span key={key} className="rd-entry__chip">{t(entry.result_labels[i] ?? key, {}, humanise(key))}</span>
        ))}
      </h2>
      {entry.lines.map((line, i) => (
        <p key={i} className="rd-entry__line">{line}</p>
      ))}
      {entry.folded && !open && <Button variant="secondary" onClick={() => setOpen(true)}>{w("transcript.unfold")}</Button>}
      <Link to={`/cases/${caseId}/read/${entry.page_no}`}>{w("transcript.see_page")}</Link>
      {open && <PagePicture caseId={caseId} pageNo={entry.page_no} onSeen={onSeen} />}
    </article>
  );
}

export function TranscriptRead() {
  const { item, pack } = useCaseFrame();
  const caseId = item.id;
  const stepUp = useStepUp();
  const [entries, setEntries] = useState<Entry[]>([]);
  const [total, setTotal] = useState(0);
  const [picturePages, setPicturePages] = useState<number[]>([]);
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
        setPicturePages(got.picture_pages);
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

  const seen = useCallback<SeenFn>(
    (view) => {
      void unwrap(api.POST("/v1/cases/{case_id}/transcript/seen", { params: { path: { case_id: caseId } }, body: { dwell_ms: 0, ...view } }))
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
      {picturePages.length > 0 && (
        <section aria-label={w("transcript.picture_pages")}>
          <h2>{w("transcript.picture_pages")}</h2>
          <p>{w("transcript.picture_pages_lead")}</p>
          {picturePages.map((n) => (
            <article key={n} className="rd-entry" aria-label={w("transcript.picture_page_label", { page: n })}>
              <p className="rd-entry__page">{w("read.page_ref", { page: n })}</p>
              <PagePicture caseId={caseId} pageNo={n} onSeen={seen} />
            </article>
          ))}
        </section>
      )}
      {entries.length < total && <Button variant="secondary" onClick={() => void load(entries.length)}>{w("transcript.more")}</Button>}
    </div>
  );
}
