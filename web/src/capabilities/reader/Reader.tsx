// Screen 09, Reading. The expert reads the page and marks it read; only then does the server release
// the suggestions (blind read, rule 3: the page asks for them after the read and shows none on a 423).
// Every action sends the version it loaded (If-Match). The server decides; this screen shows.
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, LinkButton, LoadError, Loading, Notice, PageRef, ProgressBar } from "../../foundation/ui";
import { casePath, count, periodRange, sourceLabel } from "./data";
import { suggestTimelineDate } from "./timelineDate";
import { allowed, editableFields, FactCard } from "./FactCard";
import { FactSwitcher } from "./FactSwitcher";
import { loadSplitPct, saveSplitPct } from "./splitPane";
import { SplitDivider } from "./SplitDivider";
import { Highlights } from "./Highlights";
import { PageImage } from "./PageImage";
import { forgetPage } from "./pageImageCache";
import { inPageOrder, TypedWordToggles, WordToggles, type PageWord } from "./WordPicker";
import { w } from "./words";

type Fact = Schemas["FactOut"];
type Action = "correct" | "not_right" | "edit";
type EditDraft = { id: string; text: string; words: string[]; fields: Record<string, string> };
const ZOOMS = [75, 100, 125, 150, 200, 250];

function fieldDraft(fact: Fact): Record<string, string> {
  const out: Record<string, string> = {};
  for (const row of editableFields(fact)) out[row.key] = row.value;
  return out;
}

export function Reader() {
  const { pageNo } = useParams();
  const { item } = useCaseFrame();
  if (!pageNo) return <FirstUnread caseId={item.id} />;
  const n = Number(pageNo);
  return Number.isInteger(n) && n > 0 ? <ReadPage key={n} pageNo={n} /> : <Navigate to={`/cases/${item.id}/read`} replace />;
}

/** `read` with no page: open the first page not yet read (else page 1). */
function FirstUnread({ caseId }: { caseId: string }) {
  const first = useResource(
    () => unwrap(api.GET("/v1/cases/{case_id}/pages", { params: { path: { case_id: caseId }, query: { filter: "unread", limit: 1 } } })),
    [caseId],
  );
  if (first.error) return <LoadError onRetry={first.reload} />;
  if (!first.data) return <Loading />;
  return <Navigate to={`/cases/${caseId}/read/${first.data.pages[0]?.page_no ?? 1}`} replace />;
}

const typing = (t: EventTarget | null) => {
  const el = t as HTMLElement | null;
  return !!el && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable);
};

function ReadPage({ pageNo }: { pageNo: number }) {
  const { item, pack } = useCaseFrame();
  const caseId = item.id;
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const factFromUrl = searchParams.get("fact");
  const opened = useRef(Date.now());
  const detail = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/pages/{page_no}", { params: { path: { case_id: caseId, page_no: pageNo } } })), [caseId, pageNo]);
  const progress = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/progress", casePath(caseId))), [caseId]);
  // The next page that exists (none after the last page), so Next and J stop there.
  const following = useResource(
    () => unwrap(api.GET("/v1/cases/{case_id}/pages", { params: { path: { case_id: caseId }, query: { cursor: pageNo, limit: 1 } } })).then((r) => r.pages[0]?.page_no ?? null),
    [caseId, pageNo],
  );
  const queue = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/pages/unreadable", casePath(caseId))).catch(() => null), [caseId]);
  const [read, setRead] = useState(false);
  const [facts, setFacts] = useState<Fact[] | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [editing, setEditing] = useState<EditDraft | null>(null);
  const [pageWords, setPageWords] = useState<PageWord[] | null>(null);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const workRef = useRef<HTMLDivElement>(null);
  const [splitPct, setSplitPct] = useState(loadSplitPct);
  const [info, setInfo] = useState<string | null>(null);
  const [zoom, setZoom] = useState(100);
  const [why, setWhy] = useState(false);
  const [imageKey, setImageKey] = useState(0);
  const [timelineDraft, setTimelineDraft] = useState<{ id: string; value: string } | null>(null);

  const loadFacts = useCallback(async () => {
    try {
      const { facts: list } = await unwrap(api.GET("/v1/cases/{case_id}/pages/{page_no}/facts", { params: { path: { case_id: caseId, page_no: String(pageNo) } } }));
      // The server answers only once the page was read, so the page counts as read from here.
      setRead(true);
      setFacts(list);
      setFocus((f) => {
        if (f) return f;
        if (factFromUrl && list.some((x) => x.id === factFromUrl)) return factFromUrl;
        return list.find((x) => x.status === "suggested")?.id ?? list[0]?.id ?? null;
      });
    } catch (error) {
      if (error instanceof ApiError && error.status === 423) setFacts(null);
      else setNotice(errorMessage(error));
    }
  }, [caseId, pageNo, factFromUrl]);

  // The page says whether it was read; suggestions are asked for only when it was.
  useEffect(() => {
    if (!detail.data) return;
    setRead(detail.data.read);
    if (detail.data.read) void loadFacts();
  }, [detail.data, loadFacts]);

  async function markRead() {
    if (busy || read) return;
    setBusy(true);
    setNotice(null);
    try {
      await unwrap(
        api.POST("/v1/cases/{case_id}/pages/{page_no}/read", {
          params: { path: { case_id: caseId, page_no: pageNo } },
          body: { dwell_seconds: Math.max(0, Math.round((Date.now() - opened.current) / 1000)) },
        }),
      );
      setRead(true);
      await loadFacts();
      progress.reload();
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  // "Change a detail": the cited words start chosen, except on a "Please check" fact, whose words must be
  // chosen again (the server refuses its change without them). The page's words load once.
  async function startEdit(fact: Fact) {
    setEditing({ id: fact.id, text: fact.value ?? "", words: fact.needs_recheck ? [] : fact.word_ids, fields: fieldDraft(fact) });
    if (pageWords !== null) return;
    try {
      const { words } = await unwrap(api.GET("/v1/cases/{case_id}/pages/{page_no}/words", { params: { path: { case_id: caseId, page_no: String(pageNo) } } }));
      setPageWords(words);
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }

  const toggleWord = (id: string) =>
    setEditing((e) => e && { ...e, words: e.words.includes(id) ? e.words.filter((x) => x !== id) : [...e.words, id] });

  function advanceFrom(list: Fact[], id: string) {
    const after = list.slice(list.findIndex((f) => f.id === id) + 1).find((f) => f.status === "suggested");
    setFocus(after?.id ?? id);
  }

  async function act(
    fact: Fact,
    action: Action,
    extra: { new_value?: string; word_ids?: string[]; reason?: string; fields?: Record<string, unknown>; event_date?: string } = {},
  ) {
    if (busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const next = await unwrap(
        api.POST("/v1/cases/{case_id}/facts/{fact_id}/verify", {
          params: { path: { case_id: caseId, fact_id: fact.id } },
          headers: ifMatch(`"${fact.version}"`),
          body: { action, ...extra },
        }),
      );
      const list = (facts ?? []).map((f) => (f.id === next.id ? { ...next, related: f.related } : f));
      setFacts(list);
      setEditing(null);
      setTimelineDraft(null);
      advanceFrom(list, next.id);
    } catch (error) {
      if (error instanceof ApiError && error.status === 412) {
        setNotice(w("read.error.stale"));
        await loadFacts();
      } else if (error instanceof ApiError && error.status === 422) {
        setNotice(w(error.code === "words_needed" ? "read.error.words_needed" : error.code === "invalid_fields" ? "read.error.invalid_fields" : "read.error.not_in_page"));
      } else {
        setNotice(errorMessage(error, {}));
      }
    } finally {
      setBusy(false);
    }
  }

  const notRight = (f: Fact) => act(f, "not_right", reasons[f.id]?.trim() ? { reason: reasons[f.id]!.trim() } : {});
  const orderedWords = (ids: string[]) => (pageWords ? inPageOrder(ids, pageWords) : ids);

  const saveEdit = (f: Fact) => {
    if (!editing || editing.id !== f.id) return;
    const headline = editing.text.trim();
    const valueChanged = headline !== (f.value ?? "").trim();
    const fieldsChanged = editableFields(f).some((row) => (editing.fields[row.key] ?? "").trim() !== row.value.trim());
    const wordsChanged = orderedWords(editing.words).join() !== orderedWords(f.word_ids).join();
    if (!valueChanged && !fieldsChanged && !wordsChanged) return;
    if (editing.words.length === 0 && (valueChanged || f.needs_recheck)) return;
    const body: { new_value?: string; word_ids?: string[]; fields?: Record<string, unknown> } = {};
    if (valueChanged) {
      if (!headline) return;
      body.new_value = headline;
      body.word_ids = orderedWords(editing.words);
    } else if (wordsChanged) body.word_ids = orderedWords(editing.words);
    if (fieldsChanged) body.fields = { ...(f.fields ?? {}), ...editing.fields };
    void act(f, "edit", body);
  };

  const editCanSave = (f: Fact) => {
    if (!editing || editing.id !== f.id) return false;
    const valueChanged = editing.text.trim() !== (f.value ?? "").trim();
    const fieldsChanged = editableFields(f).some((row) => (editing.fields[row.key] ?? "").trim() !== row.value.trim());
    const wordsChanged = orderedWords(editing.words).join() !== orderedWords(f.word_ids).join();
    if (!valueChanged && !fieldsChanged && !wordsChanged) return false;
    if (editing.words.length === 0 && (valueChanged || f.needs_recheck)) return false;
    if (valueChanged && !editing.text.trim()) return false;
    return true;
  };

  async function toggleFlag(fact: Fact) {
    if (busy) return;
    setBusy(true);
    setNotice(null);
    const call = fact.flagged ? api.DELETE : api.POST;
    try {
      const out = await unwrap(call("/v1/cases/{case_id}/facts/{fact_id}/flag-point", { params: { path: { case_id: caseId, fact_id: fact.id } } }));
      setFacts((list) => (list ?? []).map((f) => (f.id === out.fact_id ? { ...f, flagged: out.flagged } : f)));
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function turnPage() {
    if (busy || !detail.data) return;
    setBusy(true);
    setNotice(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/pages/{page_id}/rotate", { params: { path: { case_id: caseId, page_id: detail.data.page_id } }, body: { degrees: 90 } }));
      setInfo(w("read.turning"));
      forgetPage(caseId, pageNo);
      setImageKey((k) => k + 1);
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function askClearerCopy() {
    if (busy) return;
    setBusy(true);
    setNotice(null);
    try {
      await unwrap(api.PATCH("/v1/cases/{case_id}/pages/{page_no}", { params: { path: { case_id: caseId, page_no: pageNo } }, body: { needs_clearer_copy: true } }));
      setInfo(w("read.clearer_asked"));
      queue.reload();
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  const nextPage = following.data ?? null;
  const go = (n: number) => n >= 1 && (n <= pageNo || n === nextPage) && navigate(`/cases/${caseId}/read/${n}`);
  const latest = useRef({ markRead, go, act, notRight, startEdit, facts, focus, read, nextPage });
  latest.current = { markRead, go, act, notRight, startEdit, facts, focus, read, nextPage };

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
      const s = latest.current;
      const key = e.key.toLowerCase();
      const target = s.facts?.find((f) => f.id === s.focus);
      if (key === "j") s.go(s.nextPage ?? 0);
      else if (key === "k") s.go(pageNo - 1);
      else if (key === "r") void s.markRead();
      else if (key === "arrowright") s.go(s.nextPage ?? 0);
      else if (key === "arrowleft") s.go(pageNo - 1);
      else if ((key === "1" || key === "c") && target && allowed(target).correct) void s.act(target, "correct");
      else if ((key === "2" || key === "n") && target && allowed(target).notRight) void s.notRight(target);
      else if (key === "3" && target && allowed(target).edit) void s.startEdit(target);
      else return;
      e.preventDefault();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [pageNo]);

  if (detail.error) return <LoadError onRetry={detail.reload} />;
  if (!detail.data) return <Loading />;
  const page = detail.data;
  const left = progress.data ? Math.max(0, progress.data.total - progress.data.read) : 0;
  const hard = queue.data ? queue.data.total - queue.data.done : 0;
  const focusedFact = facts?.find((f) => f.id === focus) ?? facts?.[0] ?? null;

  return (
    <div className="rd">
      {page.in_period && (
        <div className="rd-banner">
          <div className="rd-banner__row">
            <svg className="rd-banner__eye" width="24" height="24" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" />
              <circle cx="12" cy="12" r="3" />
            </svg>
            <p>
              <strong>{w("read.in_period")}</strong>{page.period_start && page.period_end ? ` ${periodRange(page.period_start, page.period_end, pack.locale)} ` : " "}{read ? w("read.in_period_after") : ""}
            </p>
            <button type="button" className="av-text-link rd-why" aria-expanded={why} onClick={() => setWhy((v) => !v)}>
              {w("read.why")}
            </button>
          </div>
          {why && <p className="rd-why-text">{w("read.why_text")}</p>}
        </div>
      )}
      <div className="rd-work" ref={workRef} style={{ "--rd-split": `${splitPct}%` } as CSSProperties}>
        <section className="rd-pane" aria-label={w("read.page_alt", { page: pageNo })}>
          <div className="rd-bar">
            <p className="rd-source">
              <strong>{sourceLabel(pack, page.source_type)}</strong>
            </p>
            <div className="rd-zoom">
              <Button variant="secondary" aria-label={w("read.zoom_out")} disabled={zoom <= ZOOMS[0]!} onClick={() => setZoom(ZOOMS[Math.max(0, ZOOMS.indexOf(zoom) - 1)]!)}>
                −
              </Button>
              <span className="rd-ref" aria-live="polite">{w("read.zoom_level", { percent: zoom })}</span>
              <Button variant="secondary" aria-label={w("read.zoom_in")} disabled={zoom >= ZOOMS[ZOOMS.length - 1]!} onClick={() => setZoom(ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.indexOf(zoom) + 1)]!)}>
                +
              </Button>
            </div>
            <div className="rd-tools">
              <Button variant="secondary" disabled={busy} onClick={() => void turnPage()}>
                {w("read.turn")}
              </Button>
              <Button variant="secondary" disabled={busy} onClick={() => void askClearerCopy()}>
                {w("read.clearer")}
              </Button>
              <LinkButton variant="secondary" to={`/cases/${caseId}/bundle/not-this-patient?page=${pageNo}`}>
                {w("read.not_patient")}
              </LinkButton>
            </div>
          </div>
          <PageImage key={imageKey} caseId={caseId} pageNo={pageNo} zoom={zoom}>
            <Highlights facts={facts ?? []} focus={focus} />
            {editing && pageWords && <WordToggles words={pageWords} chosen={editing.words} onToggle={toggleWord} />}
          </PageImage>
        </section>

        <SplitDivider workRef={workRef} value={splitPct} onChange={setSplitPct} onCommit={saveSplitPct} />

        <aside className="rd-side" aria-label={w("read.found")}>
          <div className="rd-side__head">
            <h2>{w("read.found")}</h2>
            <PageRef page={pageNo} />
          </div>
          {notice && <Notice tone="error">{notice}</Notice>}
          {info && !notice && <Notice tone="success">{info}</Notice>}
          {facts === null && <p className="rd-note">{w("read.not_read_yet")}</p>}
          {facts?.length === 0 && <p className="rd-note">{w("read.none_found")}</p>}
          {facts && facts.length > 0 && <FactSwitcher facts={facts} focus={focus} pack={pack} onFocus={setFocus} />}
          {focusedFact && (
            <FactCard
              key={focusedFact.id}
              fact={focusedFact}
              pack={pack}
              allFacts={facts ?? []}
              focused={true}
              busy={busy}
              editing={editing?.id === focusedFact.id ? editing.text : null}
              editingFields={editing?.id === focusedFact.id ? editing.fields : null}
              canSave={editCanSave(focusedFact)}
              typedWords={editing?.id === focusedFact.id && pageWords ? <TypedWordToggles words={pageWords} chosen={editing.words} onToggle={toggleWord} /> : undefined}
              reason={reasons[focusedFact.id] ?? ""}
              onFocus={() => setFocus(focusedFact.id)}
              onReason={(text) => setReasons((r) => ({ ...r, [focusedFact.id]: text }))}
              onCorrect={() => act(focusedFact, "correct")}
              onNotRight={() => notRight(focusedFact)}
              onStartEdit={() => void startEdit(focusedFact)}
              onEditText={(text) => setEditing((e) => e && { ...e, text })}
              onEditField={(key, value) => setEditing((e) => e && { ...e, fields: { ...e.fields, [key]: value } })}
              onSaveEdit={() => saveEdit(focusedFact)}
              onCancelEdit={() => setEditing(null)}
              onFlag={() => toggleFlag(focusedFact)}
              timelineDraft={timelineDraft?.id === focusedFact.id ? timelineDraft.value : null}
              onStartTimelineDate={() => {
                setEditing(null);
                setTimelineDraft({ id: focusedFact.id, value: suggestTimelineDate(focusedFact, pack) });
              }}
              onTimelineDateChange={(value) => setTimelineDraft((d) => d && d.id === focusedFact.id ? { ...d, value } : d)}
              onSaveTimelineDate={() => {
                if (!timelineDraft || timelineDraft.id !== focusedFact.id || !timelineDraft.value) return;
                void act(focusedFact, "edit", { event_date: timelineDraft.value });
                setTimelineDraft(null);
              }}
              onCancelTimelineDate={() => setTimelineDraft(null)}
            />
          )}
          {read && (
            <div className="rd-keys">
              <p>
                <strong>{w("read.keys.heading")}</strong>
              </p>
              <p className="rd-hints">
                <span><kbd>C</kbd> {w("read.keys.correct")}</span>
                <span><kbd>N</kbd> {w("read.keys.not_right")}</span>
                <span><kbd>→</kbd> {w("read.keys.next")}</span>
              </p>
              <p>{w("read.keys.more")}</p>
            </div>
          )}
        </aside>
      </div>

      <footer className="rd-foot">
        <Button variant="secondary" arrow="back" disabled={pageNo <= 1} onClick={() => go(pageNo - 1)}>
          {w("read.previous")}
        </Button>
        <div className="rd-progress">
          {progress.data && (
            <>
              <p>
                {left === 0
                  ? w("read.progress_all", { page: pageNo, total: count(progress.data.total, pack.locale) })
                  : w("read.progress", { page: pageNo, total: count(progress.data.total, pack.locale), read: count(progress.data.read, pack.locale), left: count(left, pack.locale) })}
              </p>
              <ProgressBar max={progress.data.total || 1} value={progress.data.read} label={w("read.progress_done", { read: progress.data.read, total: progress.data.total })} />
            </>
          )}
        </div>
        {hard > 0 && (
          <Link className="rd-hard" to={`/cases/${caseId}/read/couldnt-read`}>
            {w("read.couldnt_read", { count: hard })}
          </Link>
        )}
        {read ? (
          <Button arrow disabled={nextPage === null} onClick={() => nextPage !== null && go(nextPage)}>
            {w("read.next")}
          </Button>
        ) : (
          <Button disabled={busy} onClick={() => void markRead()}>{w("read.mark_read")}</Button>
        )}
      </footer>
    </div>
  );
}
