// Screen 9b, Reading by transcript (ADR 0028): every entry in date order. The browser only reports how long
// each entry was on screen; the server decides when a page counts as read, and suggestions never show here.
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorMessage } from "../../foundation/lib/errors";
import { humanise, t } from "../../foundation/i18n";
import { formatDay } from "../../foundation/i18n/dates";
import { useStepUp } from "../../foundation/lib/stepUp";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, LinkButton, LoadError, Loading, Menu, MenuItem, MenuSection, Notice, ProgressBar, Spinner } from "../../foundation/ui";
import { count } from "./data";
import { smallPageImage } from "../../foundation/lib/pageImageCache";
import { w } from "./words";
import { saveBlob } from "../../foundation/lib/files";

type Entry = Schemas["TranscriptEntryOut"];
const LIMIT = 50;
const POLL_MS = 3000;
// Seconds' worth of milliseconds an entry must stay in view before it is reported.
const REPORT_AFTER_MS = 400;
// After this long with no change the screen says so and offers to start again.
const SLOW_AFTER_MS = 3 * 60 * 1000;

/** Milliseconds since the wait began, ticking once a second; it starts again when `progressKey` changes. */
function useElapsed(progressKey: number): number {
  const started = useRef(Date.now());
  const [, tick] = useState(0);
  useEffect(() => {
    started.current = Date.now();
    tick((n) => n + 1);
  }, [progressKey]);
  useEffect(() => {
    const timer = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  return Date.now() - started.current;
}

interface WaitProps {
  /** The sentence that says what is being waited for. */
  words: string;
  /** Determinate progress, when the server can count: the line and the numbers. */
  counted?: { label: string; value: number; max: number };
  onRetry?: () => void;
  /** Where to look when it is slow and there is nothing to retry. */
  progressTo?: string;
}

/** A spinner with honest progress: a count when the server has one, else how long it has been working. */
function WaitNotice({ words, counted, onRetry, progressTo }: WaitProps) {
  const ms = useElapsed(counted?.value ?? 0);
  const seconds = Math.floor(ms / 1000);
  const elapsed = seconds >= 60 ? w("transcript.working_for", { minutes: Math.floor(seconds / 60), seconds: seconds % 60 }) : w("transcript.working_for_seconds", { seconds });
  const slow = ms >= SLOW_AFTER_MS;
  return (
    <Notice tone="info">
      <div className="rd-wait">
        <Spinner />
        <div className="rd-wait__text">
          <p>{words}</p>
          {counted ? (
            <>
              <p>{counted.label}</p>
              <ProgressBar value={counted.value} max={counted.max} label={counted.label} />
            </>
          ) : (
            <p>{elapsed}</p>
          )}
          {slow && <p>{w("transcript.slow")}</p>}
          {slow && onRetry && <Button variant="secondary" onClick={onRetry}>{w("transcript.try_again")}</Button>}
          {slow && !onRetry && progressTo && <LinkButton variant="secondary" to={progressTo}>{w("transcript.pipeline_stopped_link")}</LinkButton>}
        </div>
      </div>
    </Notice>
  );
}

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
  const day = entry.event_date ? formatDay(entry.event_date, undefined, "long") : w("transcript.undated");
  return (
    <article ref={ref} id={`entry-${entry.id}`} tabIndex={-1} className={`rd-entry${entry.folded ? " rd-entry--folded" : ""}`} aria-label={w("transcript.entry_label", { page: entry.page_no })}>
      <h3 className="rd-entry__head">
        <span className="rd-entry__page">{w("read.page_ref", { page: entry.page_no })}</span> {day} · {t(entry.kind_label, {}, humanise(entry.kind))}
        {entry.results.map((key, i) => (
          <span key={key} className="rd-entry__chip">{t(entry.result_labels[i] ?? key, {}, humanise(key))}</span>
        ))}
      </h3>
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
  const [unreadPages, setUnreadPages] = useState<number[]>([]);
  const [status, setStatus] = useState<Schemas["TranscriptList"]["status"] | null>(null);
  const [wait, setWait] = useState<Schemas["TranscriptList"] | null>(null);
  const [failed, setFailed] = useState(false);
  const [read, setRead] = useState<number | null>(null);
  const [pagesTotal, setPagesTotal] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [preparingFile, setPreparingFile] = useState(false);
  const [outline, setOutline] = useState<Schemas["TranscriptOutline"] | null>(null);
  const entriesRef = useRef<Entry[]>([]);
  entriesRef.current = entries;
  const outlineAsked = useRef(false);

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
        setWait(got);
        setTotal(got.total);
        setPicturePages(got.picture_pages);
        setUnreadPages(got.unread_pages);
        entriesRef.current = offset === 0 ? got.entries : [...entriesRef.current, ...got.entries]; // read at once by showPosition
        setEntries(entriesRef.current);
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
    if (status !== "preparing" && !(status === "idle" && wait?.pipeline_running)) return;
    const timer = setTimeout(() => void load(0), POLL_MS);
    return () => clearTimeout(timer);
  }, [status, wait, load, entries]);

  // The shape of the transcript (sections and coverage) is fetched once, when the transcript is ready.
  useEffect(() => {
    if (status !== "ready" || outlineAsked.current) return;
    outlineAsked.current = true;
    void unwrap(api.GET("/v1/cases/{case_id}/transcript/outline", { params: { path: { case_id: caseId } } }))
      .then(setOutline)
      .catch(() => undefined);
  }, [status, caseId]);

  // Load entries until the one at `offset` is on screen, then move the reader to the heading of its section.
  // Jumping records nothing as read: only the entries on screen do (rule 3).
  async function showPosition(offset: number, key: string) {
    let have = entriesRef.current.length;
    while (have <= offset) {
      await load(have);
      if (entriesRef.current.length <= have) break;
      have = entriesRef.current.length;
    }
    await new Promise((r) => setTimeout(r, 0));
    const target = document.getElementById(`section-${key}`);
    target?.scrollIntoView({ block: "start" });
    target?.focus();
  }

  // Only the reader's own press asks the server to prepare the transcript again; polling never does.
  async function retry() {
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/transcript/retry", { params: { path: { case_id: caseId } } }));
    } catch {
      /* the next load shows the state */
    }
    await load(0);
  }

  const seen = useCallback<SeenFn>(
    (view) => {
      void unwrap(api.POST("/v1/cases/{case_id}/transcript/seen", { params: { path: { case_id: caseId } }, body: { dwell_ms: 0, ...view } }))
        .then((r) => (r.pages_read.length ? progress() : undefined))
        .catch(() => undefined);
    },
    [caseId, progress],
  );

  async function download(format: "docx" | "pdf") {
    setMessage(null);
    setPreparingFile(true);
    try {
      const started = await stepUp(() => unwrap(api.POST("/v1/cases/{case_id}/transcript/download", { params: { path: { case_id: caseId } }, body: { format } })));
      if (!started) return;
      const job_id = started.job_id;
      const tries = format === "pdf" ? 400 : 60;
      for (let n = 0; n < tries; n += 1) {
        try {
          const blob = await unwrap(api.GET("/v1/cases/{case_id}/transcript/download/{job_id}", { params: { path: { case_id: caseId, job_id } }, parseAs: "blob" }));
          saveBlob(blob as Blob, `transcript-${caseId.slice(0, 8)}.${format}`);
          setMessage(w(format === "pdf" ? "transcript.downloaded_pdf" : "transcript.downloaded"));
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

  function waitState() {
    const all = wait?.total_pages ?? 0;
    if (wait?.waiting_for === "pipeline") {
      const have = wait.extracted_pages ?? 0;
      if (wait.pipeline_running) {
        return <WaitNotice words={w("transcript.preparing")} counted={all > 0 ? { label: w("transcript.pipeline_progress", { read: count(have, pack.locale), total: count(all, pack.locale) }), value: have, max: all } : undefined} />;
      }
      return (
        <Notice tone="warning">
          <p>{w("transcript.pipeline_stopped", { waiting: count(all - have, pack.locale), total: count(all, pack.locale) })}</p>
          <LinkButton variant="secondary" to={`/cases/${caseId}/preparing`}>{w("transcript.pipeline_stopped_link")}</LinkButton>
        </Notice>
      );
    }
    if (status === "failed") {
      return (
        <Notice tone="warning">
          <p>{w("transcript.failed")}</p>
          {wait?.can_prepare !== false && <Button onClick={() => void retry()}>{w("transcript.try_again")}</Button>}
        </Notice>
      );
    }
    if (status === "idle" && wait?.can_prepare === false) return <Notice tone="info"><p>{w("transcript.ask_expert")}</p></Notice>;
    const done = wait?.prepared_pages ?? 0;
    return (
      <WaitNotice
        words={w("transcript.preparing")}
        counted={all > 0 && done > 0 ? { label: w("transcript.preparing_page", { done: count(done, pack.locale), total: count(all, pack.locale) }), value: done, max: all } : undefined}
        onRetry={status === "idle" && wait?.can_prepare !== false ? () => void retry() : undefined}
        progressTo={`/cases/${caseId}/preparing`}
      />
    );
  }

  if (failed && entries.length === 0) return <LoadError onRetry={() => void load(0)} />;
  if (status === null) return <Loading />;
  return (
    <div className="rd-transcript">
      <header className="rd-transcript__bar">
        <h1>{w("transcript.heading")}</h1>
        <div className="rd-transcript__actions">
          <LinkButton variant="secondary" to={`/cases/${caseId}/read`}>{w("transcript.page_by_page")}</LinkButton>
          <Menu label={w("transcript.download_menu")} trigger={w("transcript.download_menu")}>
            <MenuSection>
              <MenuItem onClick={() => void download("docx")} disabled={preparingFile || status !== "ready"}>
                {preparingFile ? w("transcript.preparing_file") : w("transcript.download")}
              </MenuItem>
              <MenuItem onClick={() => void download("pdf")} disabled={preparingFile || status !== "ready"}>
                {preparingFile ? w("transcript.preparing_file") : w("transcript.download_pdf")}
              </MenuItem>
              <p className="rd-transcript__note">{w("transcript.file_note")}</p>
            </MenuSection>
          </Menu>
        </div>
        {read !== null && pagesTotal > 0 && (
          <div className="rd-transcript__progress">
            <p>{w("read.read_count", { read: count(read, pack.locale), total: count(pagesTotal, pack.locale) })}</p>
            <ProgressBar max={pagesTotal || 1} value={read} label={w("read.progress_done", { read, total: pagesTotal })} />
          </div>
        )}
        <p className="rd-transcript__lead">{w("transcript.lead")}</p>
      </header>
      {message && <Notice tone="info"><p>{message}</p></Notice>}
      {outline?.coverage && (
        <p className="rd-transcript__coverage">
          {w("transcript.coverage", {
            total: outline.coverage.total_pages,
            with_entries: outline.coverage.with_entries,
            no_entries: outline.coverage.no_entries,
            unreadable: outline.coverage.unreadable,
            set_aside: outline.coverage.set_aside,
          })}
        </p>
      )}
      {outline && outline.sections.length > 0 && (
        <nav className="rd-transcript__jump" aria-label={w("transcript.jump_to")}>
          <p>{w("transcript.jump_to")}</p>
          <ul>
            {outline.sections.map((section) => (
              <li key={section.key}>
                <Button variant="link" onClick={() => void showPosition(section.offset, section.key)}>
                  {t(section.label, {}, humanise(section.key))} ({section.count})
                </Button>
              </li>
            ))}
          </ul>
        </nav>
      )}
      {status !== "ready" && waitState()}
      {status === "ready" && entries.length === 0 && <p>{w("transcript.none")}</p>}
      {entries.map((entry, i) => {
        const startsSection = entry.section && entry.section !== entries[i - 1]?.section;
        const info = startsSection ? outline?.sections.find((s) => s.key === entry.section) : undefined;
        return (
          <Fragment key={entry.id}>
            {startsSection && (
              <h2 id={`section-${entry.section}`} tabIndex={-1} className="rd-transcript__section">
                {t(info?.label ?? "", {}, humanise(entry.section ?? ""))}
              </h2>
            )}
            <EntryCard entry={entry} caseId={caseId} onSeen={seen} />
          </Fragment>
        );
      })}
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
      {unreadPages.length > 0 && (
        <section aria-label={w("transcript.unread_pages")}>
          <h2>{w("transcript.unread_pages")}</h2>
          {unreadPages.map((n) => (
            <article key={n} className="rd-entry" aria-label={w("transcript.unread_page_label", { page: n })}>
              <p className="rd-entry__page">{w("read.page_ref", { page: n })}</p>
              <p>{w("transcript.unread_page_lead")}</p>
              <PagePicture caseId={caseId} pageNo={n} onSeen={seen} />
            </article>
          ))}
        </section>
      )}
      {entries.length < total && <Button variant="secondary" onClick={() => void load(entries.length)}>{w("transcript.more")}</Button>}
    </div>
  );
}
