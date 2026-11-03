// Screen 9b, Reading by transcript (ADR 0028): every entry in date order, in chapters (the contents, a section, a list
// of pages). The browser only reports how long each entry was on screen; the server decides when a page counts as read,
// and suggestions never show here. Where you are lives in the address (`?section=`), so Back works.
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorMessage } from "../../foundation/lib/errors";
import { humanise, t } from "../../foundation/i18n";
import { formatDay } from "../../foundation/i18n/dates";
import { useStepUp } from "../../foundation/lib/stepUp";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, LinkButton, LoadError, Loading, Menu, MenuItem, MenuSection, Notice, ProgressBar, Spinner } from "../../foundation/ui";
import { count } from "./data";
import { pageImage } from "../../foundation/lib/pageImageCache";
import { w } from "./words";
import { saveBlob } from "../../foundation/lib/files";
import { appendices as appendixLists, NO_ENTRIES, TranscriptContents, UNREAD } from "./TranscriptContents";
import { PAGE_SIZE, TranscriptSection, yearId } from "./TranscriptSection";

type Entry = Schemas["TranscriptEntryOut"];
// Entries asked for in one go when the reader jumps to a year that is not loaded yet.
const JUMP_LIMIT = 200;
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

/** The picture of a page, opened by the reader (the full picture: a page the computer could not read is often faint or handwritten, and a small one is blurry); opening it is reported (the way a page with no entries is read). */
function PagePicture({ caseId, pageNo, onSeen }: { caseId: string; pageNo: number; onSeen: SeenFn }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  async function open() {
    try {
      setSrc(await pageImage(caseId, pageNo));
      setFailed(false);
      onSeen({ page_no: pageNo, picture_opened: true });
    } catch {
      setFailed(true);
    }
  }
  if (src) {
    return (
      <>
        <img className="rd-entry__picture" src={src} alt={w("transcript.picture_alt", { page: pageNo })} />
        <p><Link to={`/cases/${caseId}/read/${pageNo}`}>{w("transcript.see_page")}</Link></p>
      </>
    );
  }
  return (
    <>
      <Button variant="secondary" onClick={() => void open()}>{w("transcript.open_picture", { page: pageNo })}</Button>
      {failed && <p role="alert">{w("transcript.picture_failed")}</p>}
    </>
  );
}

/** `level` is the heading level of the entry's own line: 5 under a month, 4 under "No date" (which has no month). */
function EntryCard({ entry, caseId, onSeen, level }: { entry: Entry; caseId: string; onSeen: SeenFn; level: 4 | 5 }) {
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
  const day = entry.event_date ? formatDay(entry.event_date, undefined, "short") : w("transcript.undated");
  const Head = level === 4 ? "h4" : "h5";
  const author = entry.author_label ? t(entry.author_label, {}, humanise(entry.author_role ?? "")) : null;
  return (
    <article ref={ref} id={`entry-${entry.id}`} tabIndex={-1} className={`rd-entry rd-entry--row${entry.folded ? " rd-entry--folded" : ""}`} aria-label={w("transcript.entry_label", { page: entry.page_no })}>
      <div className="rd-entry__gutter">
        <Link className="rd-entry__page" to={`/cases/${caseId}/read/${entry.page_no}`}>{w("read.page_ref", { page: entry.page_no })}</Link>
        <span className="rd-entry__date">{day}</span>
      </div>
      <div className="rd-entry__body">
        <Head className="rd-entry__head">
          {t(entry.kind_label, {}, humanise(entry.kind))}
          {author ? ` · ${author}` : ""}
          {entry.results.map((key, i) => (
            <span key={key} className="rd-entry__chip">{t(entry.result_labels[i] ?? key, {}, humanise(key))}</span>
          ))}
        </Head>
        {entry.lines.map((line, i) => (
          <p key={i} className="rd-entry__line">{line}</p>
        ))}
        {entry.folded && !open && <Button variant="secondary" onClick={() => setOpen(true)}>{w("transcript.unfold")}</Button>}
        <Link to={`/cases/${caseId}/read/${entry.page_no}`}>{w("transcript.see_page")}</Link>
        {open && <PagePicture caseId={caseId} pageNo={entry.page_no} onSeen={onSeen} />}
      </div>
    </article>
  );
}

export function TranscriptRead() {
  const { item, pack } = useCaseFrame();
  const caseId = item.id;
  const stepUp = useStepUp();
  const [params, setParams] = useSearchParams();
  const key = params.get("section");
  // The entries of the open section, in the server's order, and the section they belong to.
  const [entries, setEntries] = useState<Entry[]>([]);
  const [entriesFor, setEntriesFor] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [picturePages, setPicturePages] = useState<number[]>([]);
  const [unreadPages, setUnreadPages] = useState<number[]>([]);
  const [status, setStatus] = useState<Schemas["TranscriptList"]["status"] | null>(null);
  const [wait, setWait] = useState<Schemas["TranscriptList"] | null>(null);
  // The pages the retry would try (the bundle summary's count: one definition for the job, the summary and this button).
  const [retryInfo, setRetryInfo] = useState({ failed_pages: 0, retrying: false });
  const [failed, setFailed] = useState(false);
  const [read, setRead] = useState<number | null>(null);
  const [pagesTotal, setPagesTotal] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [preparingFile, setPreparingFile] = useState(false);
  const [outline, setOutline] = useState<Schemas["TranscriptOutline"] | null>(null);
  const entriesRef = useRef<Entry[]>([]);
  entriesRef.current = entries;
  const outlineAsked = useRef(false);
  const [outlineFailed, setOutlineFailed] = useState(false);
  // Counts the sections opened, so a slow answer for a section the reader has left is dropped.
  const generation = useRef(0);
  // One request for more entries at a time: a second press while one is on its way does nothing.
  const appending = useRef(false);
  const appendToken = useRef(0);
  const [busyMore, setBusyMore] = useState(false);
  const [moreFailed, setMoreFailed] = useState(false);
  const retryMore = useRef<(() => void) | null>(null);

  const progress = useCallback(async () => {
    try {
      const p = await unwrap(api.GET("/v1/cases/{case_id}/progress", { params: { path: { case_id: caseId } } }));
      setRead(p.read);
      setPagesTotal(p.total);
    } catch {
      /* the count is a convenience */
    }
  }, [caseId]);

  // "status" only learns how the preparation is going (it also starts it); "replace" opens a section; "append" adds to it.
  const load = useCallback(
    async (offset: number, limit: number, mode: "status" | "replace" | "append", forSection?: string): Promise<boolean> => {
      const mine = mode === "replace" ? ++generation.current : generation.current;
      try {
        const got = await unwrap(api.GET("/v1/cases/{case_id}/transcript/entries", { params: { path: { case_id: caseId }, query: { offset, limit } } }));
        setFailed(false);
        setStatus(got.status);
        setWait(got);
        setPicturePages(got.picture_pages);
        setUnreadPages(got.unread_pages);
        if (mode === "status" || mine !== generation.current) return true;
        entriesRef.current = mode === "replace" ? got.entries : [...entriesRef.current, ...got.entries]; // read at once by goToYear
        setEntries(entriesRef.current);
        if (mode === "replace") setEntriesFor(forSection ?? null);
        return true;
      } catch {
        setFailed(true);
        return false;
      }
    },
    [caseId],
  );

  const retryPages = useCallback(async () => {
    try {
      const s = await unwrap(api.GET("/v1/cases/{case_id}/bundle/summary", { params: { path: { case_id: caseId } } }));
      setRetryInfo({ failed_pages: s.failed_pages, retrying: s.retrying });
    } catch {
      /* no button and no "trying again" words; the transcript still reads */
    }
  }, [caseId]);

  useEffect(() => {
    void load(0, 1, "status");
    void progress();
  }, [load, progress]);

  // Learned when the transcript changes state (not on every poll): ready shows the button, preparing the words.
  useEffect(() => {
    if (status !== null) void retryPages();
  }, [status, retryPages]);

  useEffect(() => {
    if (status !== "preparing" && !(status === "idle" && wait?.pipeline_running)) return;
    const timer = setTimeout(() => void load(0, 1, "status"), POLL_MS);
    return () => clearTimeout(timer);
  }, [status, wait, load]);

  const fetchOutline = useCallback(() => {
    setOutlineFailed(false);
    void unwrap(api.GET("/v1/cases/{case_id}/transcript/outline", { params: { path: { case_id: caseId } } }))
      .then(setOutline)
      .catch(() => setOutlineFailed(true));
  }, [caseId]);

  // The shape of the transcript (sections and coverage) is fetched once, when the transcript is ready.
  useEffect(() => {
    if (status !== "ready" || outlineAsked.current) return;
    outlineAsked.current = true;
    fetchOutline();
  }, [status, fetchOutline]);

  const sections = outline?.sections ?? [];
  const lists = appendixLists(picturePages.length, unreadPages.length);
  const section = key ? sections.find((s) => s.key === key) : undefined;
  const sectionKey = section?.key;

  // Opening a section brings its first entries, from the section's own place in the list (the server already
  // keeps each section together, so the browser filters nothing).
  useEffect(() => {
    entriesRef.current = [];
    setEntries([]);
    setEntriesFor(null);
    appendToken.current += 1; // an answer still on its way for the section left behind is dropped
    appending.current = false;
    setBusyMore(false);
    setMoreFailed(false);
    const target = outline?.sections.find((s) => s.key === sectionKey);
    if (!target || status !== "ready") return;
    if (target.count === 0) {
      setEntriesFor(target.key);
      return;
    }
    void load(target.offset, Math.min(PAGE_SIZE, target.count), "replace", target.key);
  }, [sectionKey, status, outline, reload, load]);

  // Moving to another part of the transcript puts the reader at its heading.
  const lastKey = useRef(key);
  useEffect(() => {
    if (lastKey.current === key) return;
    lastKey.current = key;
    const timer = setTimeout(() => document.getElementById("transcript-view-heading")?.focus(), 0);
    return () => clearTimeout(timer);
  }, [key]);

  const go = useCallback((to: string | null) => setParams(to ? { section: to } : {}), [setParams]);

  // Runs one request for more entries. A second press while it runs does nothing; a failure is shown, with Try again.
  async function append(run: (stillHere: () => boolean) => Promise<boolean>, again: () => void) {
    if (appending.current) return;
    appending.current = true;
    const token = ++appendToken.current;
    const stillHere = () => token === appendToken.current;
    setBusyMore(true);
    setMoreFailed(false);
    try {
      const ok = await run(stillHere);
      if (!stillHere()) return;
      retryMore.current = ok ? null : again;
      setMoreFailed(!ok);
    } finally {
      if (stillHere()) {
        appending.current = false;
        setBusyMore(false);
      }
    }
  }

  function showMore(open: NonNullable<typeof section>) {
    return append(
      () => {
        const have = entriesRef.current.length;
        return load(open.offset + have, Math.min(PAGE_SIZE, open.count - have), "append");
      },
      () => void showMore(open),
    );
  }

  // Load forward until the first entry of the year is on screen, then move the reader to its heading.
  // Jumping records nothing as read: only the entries on screen do (rule 3).
  function goToYear(open: NonNullable<typeof section>, year: Schemas["OutlineYear"]) {
    return append(
      async (stillHere) => {
        const mine = generation.current;
        const left = () => !stillHere() || mine !== generation.current;
        const wanted = year.offset - open.offset;
        let have = entriesRef.current.length;
        while (have <= wanted && have < open.count) {
          if (!(await load(open.offset + have, Math.min(JUMP_LIMIT, open.count - have), "append"))) return false;
          if (left()) return true;
          if (entriesRef.current.length <= have) break;
          have = entriesRef.current.length;
        }
        await new Promise((r) => setTimeout(r, 0));
        if (left()) return true;
        const target = document.getElementById(yearId(year.year));
        target?.scrollIntoView({ block: "start" });
        target?.focus({ preventScroll: true });
        return true;
      },
      () => void goToYear(open, year),
    );
  }

  // Only the reader's own press asks the server to prepare the transcript again; polling never does.
  async function retry() {
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/transcript/retry", { params: { path: { case_id: caseId } } }));
    } catch {
      /* the next load shows the state */
    }
    outlineAsked.current = false; // the shape of the transcript is fetched again once it is ready again
    setOutline(null);
    await load(0, 1, "status");
    await retryPages();
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
        words={w(retryInfo.retrying ? "transcript.retrying" : "transcript.preparing")}
        counted={all > 0 && done > 0 ? { label: w("transcript.preparing_page", { done: count(done, pack.locale), total: count(all, pack.locale) }), value: done, max: all } : undefined}
        onRetry={status === "idle" && wait?.can_prepare !== false ? () => void retry() : undefined}
        progressTo={`/cases/${caseId}/preparing`}
      />
    );
  }

  // The part of the transcript the address names: a section, a list of pages, or (by default) the contents.
  function view(): ReactNode {
    if (outlineFailed) return <LoadError onRetry={fetchOutline} />;
    if (!outline) return <Loading />;
    const list = lists.find((l) => l.key === key);
    if (list) {
      return (
        <div className="rd-appendix">
          <Link className="rd-back" to={{ search: "" }}>{w("transcript.back_link")}</Link>
          {list.key === NO_ENTRIES && (
            <section aria-label={w("transcript.picture_pages")}>
              <h2 id="transcript-view-heading" tabIndex={-1} className="rd-view-heading">{list.title}</h2>
              <p>{w("transcript.picture_pages_lead")}</p>
              {picturePages.map((n) => (
                <article key={n} className="rd-entry" aria-label={w("transcript.picture_page_label", { page: n })}>
                  <p className="rd-entry__page">{w("read.page_ref", { page: n })}</p>
                  <PagePicture caseId={caseId} pageNo={n} onSeen={seen} />
                </article>
              ))}
            </section>
          )}
          {list.key === UNREAD && (
            <section aria-label={w("transcript.unread_pages")}>
              <h2 id="transcript-view-heading" tabIndex={-1} className="rd-view-heading">{list.title}</h2>
              {unreadPages.map((n) => (
                <article key={n} className="rd-entry" aria-label={w("transcript.unread_page_label", { page: n })}>
                  <p className="rd-entry__page">{w("read.page_ref", { page: n })}</p>
                  <p>{w("transcript.unread_page_lead")}</p>
                  <PagePicture caseId={caseId} pageNo={n} onSeen={seen} />
                </article>
              ))}
            </section>
          )}
        </div>
      );
    }
    if (section) {
      if (failed && entriesFor !== section.key) return <LoadError onRetry={() => { setFailed(false); setReload((n) => n + 1); }} />;
      return (
        <TranscriptSection
          section={section}
          number={sections.indexOf(section) + 1}
          sections={sections}
          appendices={lists}
          entries={entriesFor === section.key ? entries : null}
          locale={pack.locale}
          renderEntry={(entry, level) => <EntryCard entry={entry} caseId={caseId} onSeen={seen} level={level} />}
          onMore={() => void showMore(section)}
          busy={busyMore}
          moreFailed={moreFailed}
          onRetryMore={() => retryMore.current?.()}
          onYear={(year) => void goToYear(section, year)}
          go={go}
        />
      );
    }
    if (sections.length === 0 && lists.length === 0) return <p>{w("transcript.none")}</p>;
    return <TranscriptContents coverage={outline.coverage ?? undefined} sections={sections} appendices={lists} locale={pack.locale} onRetryUnread={retryInfo.failed_pages > 0 && wait?.can_prepare === true ? () => void retry() : undefined} />;
  }

  if (failed && status === null) return <LoadError onRetry={() => void load(0, 1, "status")} />;
  if (status === null) return <Loading />;
  return (
    <div className={`rd-transcript${section ? " rd-transcript--wide" : ""}`}>
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
      {status !== "ready" && waitState()}
      {status === "ready" && view()}
    </div>
  );
}
