// Screen 09, Reading. The expert reads the page and marks it read; only then does the server release
// the suggestions (blind read, rule 3: the page asks for them after the read and shows none on a 423).
// Every action sends the version it loaded (If-Match). The server decides; this screen shows.
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorAction, errorMessage, type ErrorAction } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, LinkButton, LoadError, Loading, Notice, PageRef, ProgressBar } from "../../foundation/ui";
import { casePath, count, periodRange, sourceLabel } from "./data";
import { dateFieldKey, suggestTimelineDate } from "./timelineDate";
import { allowed, editableFields, FactCard } from "./FactCard";
import { FactSwitcher } from "./FactSwitcher";
import { loadSplitPct, saveSplitPct } from "./splitPane";
import { SplitDivider } from "./SplitDivider";
import { BundleSearch, SearchMarks } from "./BundleSearch";
import { Highlights } from "./Highlights";
import { MyNotes } from "./MyNotes";
import { PageImage } from "./PageImage";
import { PageStrip } from "./PageStrip";
import { noteRead, useBundlePages } from "./bundlePages";
import { loadSearch, loadTools, saveSearch, saveTools, type SearchResult, type Tools } from "./search";
import { clampZoom, loadView, saveView, turnBy, ZOOM_MAX, ZOOM_MIN, type View } from "./viewer";
import { inPageOrder, TypedWordToggles, WordToggles, type PageWord } from "./WordPicker";
import { w } from "./words";

type Fact = Schemas["FactOut"];
type Action = "correct" | "not_right" | "edit";
type EditDraft = { id: string; text: string; words: string[]; fields: Record<string, string>; eventDate: string | null; eventDateByHand: boolean; misread: boolean; misreadText: string };

type MoveDraft = { id: string; page: number; words: PageWord[] | null; chosen: string[]; problem: string | null; readFirst: boolean };

function fieldDraft(fact: Fact, pack: Schemas["CasePack"]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const row of editableFields(fact, pack)) out[row.key] = row.value;
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
  const detail = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/pages/{page_no}", { params: { path: { case_id: caseId, page_no: pageNo } } })), [caseId, pageNo], { cache: "reader/Reader#ReadPage:detail" });
  const progress = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/progress", casePath(caseId))), [caseId], { cache: "reader/Reader#ReadPage:progress" });
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
  // How the page is shown (held for the visit): fit, zoom and "Make it clearer" stay as the expert left them.
  const [view, setView] = useState<View>(loadView);
  const [scale, setScale] = useState<number | null>(null);
  const scaleRef = useRef<number | null>(null);
  scaleRef.current = scale;
  const [clearer, setClearer] = useState(false);
  // How the page is turned for reading (display only; saved for the case, nothing is read again).
  const canTurn = (item.allowed_actions ?? []).includes("page.rotate");
  const [turn, setTurn] = useState(0);
  const [turnsBefore, setTurnsBefore] = useState<number[]>([]);
  const turnLoaded = useRef(false);
  const saving = useRef<Promise<unknown>>(Promise.resolve());
  const pages = useBundlePages(caseId);
  const [gotoText, setGotoText] = useState("");
  const [gotoError, setGotoError] = useState<string | null>(null);
  const [why, setWhy] = useState(false);
  const [timelineDraft, setTimelineDraft] = useState<{ id: string; value: string } | null>(null);
  // Finding words, two pages side by side and full screen: held for the visit, from page to page.
  const [found, setFound] = useState<SearchResult | null>(() => loadSearch(caseId));
  const [tools, setTools] = useState<Tools>(loadTools);
  const [beside, setBeside] = useState<number | null>(null);
  const [besideText, setBesideText] = useState<string | null>(null);
  const [besideError, setBesideError] = useState<string | null>(null);
  const canNote = (item.allowed_actions ?? []).includes("page.read");
  // The toolbar's "More tools" panel and "Find words" (open again on the next page while a search is held).
  const [more, setMore] = useState(false);
  const moreRef = useRef<HTMLSpanElement>(null);
  const [findOpen, setFindOpen] = useState(found !== null);
  const [focusFind, setFocusFind] = useState(false);

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
    if (!turnLoaded.current) {
      turnLoaded.current = true;
      setTurn(detail.data.turn ?? 0);
    }
    setRead(detail.data.read);
    if (detail.data.read) void loadFacts();
  }, [detail.data, loadFacts]);

  // An error for the form the expert is filling in, shown under its Save button (not at the top of the panel).
  const [formError, setFormError] = useState<{ text: string; typeOut: boolean; tick?: boolean; action?: ErrorAction | null } | null>(null);
  // "Move to the clearer copy": the copy's words (once read) to choose from, and what stops the move.
  const [moving, setMoving] = useState<MoveDraft | null>(null);

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
      noteRead(caseId, pageNo);
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
    setFormError(null);
    setEditing({ id: fact.id, text: fact.value ?? "", words: fact.needs_recheck ? [] : fact.word_ids, fields: fieldDraft(fact, pack), eventDate: hasDate(fact) ? suggestTimelineDate(fact, pack) : null, eventDateByHand: false, misread: false, misreadText: "" });
    if (pageWords !== null) return;
    try {
      const { words } = await unwrap(api.GET("/v1/cases/{case_id}/pages/{page_no}/words", { params: { path: { case_id: caseId, page_no: String(pageNo) } } }));
      setPageWords(words);
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }

  const hasDate = (f: Fact) => dateFieldKey(pack, f.kind) !== null;
  const eventDateChanged = (f: Fact) => !!editing?.eventDate && editing.eventDate !== (f.event_date ?? "").slice(0, 10);

  const toggleWord = (id: string) =>
    setEditing((e) => e && { ...e, words: e.words.includes(id) ? e.words.filter((x) => x !== id) : [...e.words, id] });

  function advanceFrom(list: Fact[], id: string) {
    const after = list.slice(list.findIndex((f) => f.id === id) + 1).find((f) => f.status === "suggested");
    setFocus(after?.id ?? id);
  }

  async function act(
    fact: Fact,
    action: Action,
    extra: { new_value?: string; word_ids?: string[]; reason?: string; fields?: Record<string, unknown>; event_date?: string; corrected_words?: { word_ids: string[]; text: string } } = {},
  ) {
    if (busy) return;
    setBusy(true);
    setNotice(null);
    setFormError(null);
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
      } else {
        const inForm = editing?.id === fact.id || timelineDraft?.id === fact.id;
        const refused = error instanceof ApiError && error.status === 422;
        const notOnPage = refused && error.code !== "words_needed" && error.code !== "invalid_fields";
        const text = refused
          ? w(error.code === "words_needed" ? "read.error.words_needed" : error.code === "invalid_fields" ? "read.error.invalid_fields" : "read.error.not_in_page")
          : errorMessage(error, {});
        if (inForm) setFormError({ text, typeOut: notOnPage, tick: notOnPage && editing?.id === fact.id, action: errorAction(error, caseId) });
        else setNotice(text);
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
    const fieldsChanged = editableFields(f, pack).some((row) => (editing.fields[row.key] ?? "").trim() !== row.value.trim());
    const wordsChanged = orderedWords(editing.words).join() !== orderedWords(f.word_ids).join();
    const dayChanged = eventDateChanged(f);
    const misread = editing.misread && editing.words.length > 0 ? editing.misreadText.trim() : null;
    const stop = (key: "read.error.nothing_changed" | "read.error.words_needed" | "read.error.headline_needed" | "read.error.misread_needed") =>
      setFormError({ text: w(key), typeOut: false });
    if (valueChanged && !headline) return stop("read.error.headline_needed");
    if (misread === "") return stop("read.error.misread_needed");
    if (!valueChanged && !misread && !fieldsChanged && !wordsChanged && !dayChanged) return stop("read.error.nothing_changed");
    if (editing.words.length === 0 && (valueChanged || f.needs_recheck)) return stop("read.error.words_needed");
    setFormError(null);
    const body: { new_value?: string; word_ids?: string[]; fields?: Record<string, unknown>; event_date?: string; corrected_words?: { word_ids: string[]; text: string } } = {};
    if (misread) {
      // The expert's reading of the chosen words goes with the value it shows (the server checks the value against it).
      body.new_value = headline;
      body.word_ids = orderedWords(editing.words);
      body.corrected_words = { word_ids: body.word_ids, text: misread };
    } else if (valueChanged) {
      body.new_value = headline;
      body.word_ids = orderedWords(editing.words);
    } else if (wordsChanged) body.word_ids = orderedWords(editing.words);
    if (fieldsChanged) body.fields = { ...(f.fields ?? {}), ...editing.fields };
    if (dayChanged && editing.eventDate) body.event_date = editing.eventDate;
    void act(f, "edit", body);
  };

  async function startMove(fact: Fact, to: number) {
    setEditing(null);
    setTimelineDraft(null);
    setFormError(null);
    setMoving({ id: fact.id, page: to, words: null, chosen: [], problem: null, readFirst: false });
    try {
      const { words } = await unwrap(api.GET("/v1/cases/{case_id}/pages/{page_no}/words", { params: { path: { case_id: caseId, page_no: String(to) } } }));
      setMoving((m) => !m || m.id !== fact.id ? m : { ...m, words });
    } catch (error) {
      const blind = error instanceof ApiError && error.status === 423;
      setMoving((m) => !m || m.id !== fact.id ? m : { ...m, readFirst: blind, problem: blind ? w("fact.move_read_first", { page: to }) : errorMessage(error) });
    }
  }

  async function saveMove(fact: Fact) {
    if (!moving || moving.id !== fact.id || busy) return;
    if (moving.chosen.length === 0) {
      setMoving({ ...moving, problem: w("fact.move_no_words") });
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      await unwrap(
        api.POST("/v1/cases/{case_id}/facts/{fact_id}/move", {
          params: { path: { case_id: caseId, fact_id: fact.id } },
          headers: ifMatch(`"${fact.version}"`),
          body: { to_page_no: moving.page, word_ids: moving.words ? inPageOrder(moving.chosen, moving.words) : moving.chosen },
        }),
      );
      const list = (facts ?? []).filter((f) => f.id !== fact.id);
      setFacts(list);
      setMoving(null);
      setInfo(w("read.moved", { page: moving.page }));
      setFocus(list.find((f) => f.status === "suggested")?.id ?? list[0]?.id ?? null);
    } catch (error) {
      if (error instanceof ApiError && error.status === 412) {
        setMoving(null);
        setNotice(w("read.error.stale"));
        await loadFacts();
      } else {
        const blind = error instanceof ApiError && error.status === 423;
        const refused = error instanceof ApiError && error.status === 422;
        const problem = blind ? w("fact.move_read_first", { page: moving.page }) : refused ? w("read.error.not_in_page") : errorMessage(error);
        setMoving((m) => m && { ...m, readFirst: blind, problem });
      }
    } finally {
      setBusy(false);
    }
  }

  const editCanSave = (f: Fact) => {
    if (!editing || editing.id !== f.id) return false;
    const valueChanged = editing.text.trim() !== (f.value ?? "").trim();
    return !(editing.words.length === 0 && (valueChanged || f.needs_recheck) && editing.text.trim() !== "");
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

  useEffect(() => saveView(view), [view]);
  useEffect(() => saveTools(tools), [tools]);

  const showFound = (result: SearchResult | null) => {
    saveSearch(caseId, result);
    setFound(result);
  };

  /** Full screen: the browser's own where it has one, and the page view filling the window either way. */
  const setFull = useCallback((full: boolean) => {
    setTools((t) => ({ ...t, full }));
    try {
      if (full && !document.fullscreenElement) void document.documentElement.requestFullscreen?.()?.catch(() => undefined);
      if (!full && document.fullscreenElement) void document.exitFullscreen?.()?.catch(() => undefined);
    } catch {
      // The window-filling view still works without the browser's full screen.
    }
  }, []);
  useEffect(() => {
    const left = () => {
      if (!document.fullscreenElement) setTools((t) => (t.full ? { ...t, full: false } : t));
    };
    document.addEventListener("fullscreenchange", left);
    return () => document.removeEventListener("fullscreenchange", left);
  }, []);

  async function showBeside() {
    const n = Number((besideText ?? "").trim());
    if (!Number.isInteger(n) || n < 1) return setBesideError(w("read.goto_number"));
    // The loaded list may hold only part of the bundle: a page not in it is asked of the server.
    if (!pages?.some((p) => p.page_no === n)) {
      try {
        await unwrap(api.GET("/v1/cases/{case_id}/pages/{page_no}", { params: { path: { case_id: caseId, page_no: n } } }));
      } catch {
        return setBesideError(w("read.goto_none", { page: n }));
      }
    }
    setBesideError(null);
    setBesideText(null);
    setBeside(n);
  }

  /** Ctrl and the wheel, a pinch, or the zoom buttons: zoom from what is on screen now. */
  function zoomBy(points: number) {
    setView((v) => {
      const from = v.fit === "zoom" ? v.zoom : (scaleRef.current ?? v.zoom);
      return { ...v, fit: "zoom", zoom: clampZoom(from + points) };
    });
  }

  function showTurn(next: number, undoing: boolean) {
    if (!canTurn) return;
    if (!undoing) setTurnsBefore((list) => [...list, turn]);
    setTurn(next);
    // Saved in the order turned; a refusal is said, and the page stays as shown.
    saving.current = saving.current
      .then(() => unwrap(api.PUT("/v1/cases/{case_id}/pages/{page_no}/turn", { params: { path: { case_id: caseId, page_no: pageNo } }, body: { degrees: next as 0 | 90 | 180 | 270 } })))
      .catch((error: unknown) => setNotice(errorMessage(error)));
  }

  const turnPage = (by: number) => showTurn(turnBy(turn, by), false);
  function undoTurn() {
    const before = turnsBefore[turnsBefore.length - 1];
    if (before === undefined) return;
    setTurnsBefore((list) => list.slice(0, -1));
    showTurn(before, true);
  }

  /** A tool from More tools: do it, close the panel, and keep the keyboard on the More tools button. */
  const pick = (run: () => void) => () => {
    run();
    closeMore();
  };
  function closeMore() {
    setMore(false);
    document.getElementById("rd-more-button")?.focus();
  }

  // A click outside the More tools button and panel closes the panel (the page stays live; it is not a modal).
  useEffect(() => {
    if (!more) return;
    const away = (e: MouseEvent) => {
      if (!moreRef.current?.contains(e.target as Node)) setMore(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [more]);

  const openPage = (n: number) => n !== pageNo && navigate(`/cases/${caseId}/read/${n}`);
  async function goToPage() {
    const n = Number(gotoText.trim());
    if (!Number.isInteger(n) || n < 1) return setGotoError(w("read.goto_number"));
    if (pages) {
      if (!pages.some((p) => p.page_no === n)) return setGotoError(w("read.goto_none", { page: n }));
    } else {
      try {
        await unwrap(api.GET("/v1/cases/{case_id}/pages/{page_no}", { params: { path: { case_id: caseId, page_no: n } } }));
      } catch {
        return setGotoError(w("read.goto_none", { page: n }));
      }
    }
    setGotoError(null);
    openPage(n);
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
  const latest = useRef({ markRead, go, act, notRight, startEdit, facts, focus, read, nextPage, turnPage, undoTurn, moving, full: tools.full, setFull, more, closeMore });
  latest.current = { markRead, go, act, notRight, startEdit, facts, focus, read, nextPage, turnPage, undoTurn, moving, full: tools.full, setFull, more, closeMore };

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
      const s = latest.current;
      // Escape closes More tools first, even from its sliders.
      if (e.key === "Escape" && s.more) {
        s.closeMore();
        e.preventDefault();
        return;
      }
      if (typing(e.target)) return;
      const key = e.key.toLowerCase();
      const target = s.facts?.find((f) => f.id === s.focus);
      if (key === "t" || key === "u") {
        // Not while words are chosen on the clearer copy: that view shows another page, and this one is hidden.
        if (s.moving) return;
        if (key === "t") s.turnPage(e.shiftKey ? -90 : 90);
        else s.undoTurn();
      }
      else if (key === "f") s.setFull(!s.full);
      else if (key === "escape" && s.full) s.setFull(false);
      else if (key === "j") s.go(s.nextPage ?? 0);
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
  const besideNo = beside ?? page.superseded_by_page_no ?? nextPage ?? (pageNo > 1 ? pageNo - 1 : null);
  const filter = view.brightness === 100 && view.contrast === 100 ? undefined : `brightness(${view.brightness / 100}) contrast(${view.contrast / 100})`;

  return (
    <div className={tools.full ? "rd rd--full" : "rd"}>
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
            <div className="rd-bar__top">
              <p className="rd-source">
                <strong>{sourceLabel(pack, page.source_type)}</strong>
              </p>
              <form
                className="rd-goto"
                onSubmit={(e) => {
                  e.preventDefault();
                  void goToPage();
                }}
              >
                <label htmlFor="rd-goto" className="rd-label">
                  {w("read.goto")}
                </label>
                <input
                  id="rd-goto"
                  className="av-input rd-goto__input"
                  type="text"
                  inputMode="numeric"
                  autoComplete="off"
                  value={gotoText}
                  aria-describedby={gotoError ? "rd-goto-error" : undefined}
                  onChange={(e) => {
                    setGotoText(e.target.value);
                    setGotoError(null);
                  }}
                />
                <Button type="submit" variant="secondary">
                  {w("read.goto_go")}
                </Button>
              </form>
            </div>
            {gotoError && (
              <p id="rd-goto-error" className="rd-goto__error" role="alert">
                {gotoError}
              </p>
            )}
            {/* The few tools used most; everything else waits under More tools (one main action per screen). */}
            <div className="rd-view" role="toolbar" aria-label={w("read.view_bar")}>
              {canTurn && (
                <Button variant="secondary" className="rd-view__turn" onClick={() => turnPage(90)}>
                  <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 12a9 9 0 1 1-3-6.7" />
                    <path d="M21 3v6h-6" />
                  </svg>
                  {w("read.turn_page")}
                </Button>
              )}
              <span className="rd-view__group">
                <Button variant="secondary" aria-label={w("read.zoom_out")} disabled={view.fit === "zoom" && view.zoom <= ZOOM_MIN} onClick={() => zoomBy(-25)}>
                  −
                </Button>
                <span className="rd-ref rd-view__level" aria-live="polite">
                  {w("read.zoom_level", { percent: view.fit === "zoom" ? view.zoom : (scale ?? 100) })}
                </span>
                <Button variant="secondary" aria-label={w("read.zoom_in")} disabled={view.fit === "zoom" && view.zoom >= ZOOM_MAX} onClick={() => zoomBy(25)}>
                  +
                </Button>
              </span>
              <Button
                variant="secondary"
                aria-expanded={findOpen}
                aria-controls="rd-find"
                onClick={() => {
                  setFocusFind(!findOpen);
                  setFindOpen((o) => !o);
                }}
              >
                {w("read.find")}
              </Button>
              <span className="rd-view__anchor" ref={moreRef}>
                <Button id="rd-more-button" variant="secondary" className="rd-view__more" aria-expanded={more} aria-controls="rd-more" onClick={() => setMore((o) => !o)}>
                  {w("read.more_tools")}
                  <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d={more ? "M6 15l6-6 6 6" : "M6 9l6 6 6-6"} />
                  </svg>
                </Button>
                {more && (
                  <div id="rd-more" className="rd-more" role="group" aria-label={w("read.more_tools")}>
                    {canTurn && (
                      <div className="rd-more__group" role="group" aria-labelledby="rd-more-turning">
                        <p id="rd-more-turning" className="rd-more__head">{w("read.more.turning")}</p>
                        <Button variant="secondary" onClick={pick(() => turnPage(-90))}>
                          {w("read.turn_left")}
                        </Button>
                        <Button variant="secondary" onClick={pick(() => turnPage(180))}>
                          {w("read.upside_down")}
                        </Button>
                        <Button variant="secondary" disabled={turnsBefore.length === 0} onClick={pick(undoTurn)}>
                          {w("read.undo_turn")}
                        </Button>
                      </div>
                    )}
                    <div className="rd-more__group" role="group" aria-labelledby="rd-more-size">
                      <p id="rd-more-size" className="rd-more__head">{w("read.more.size")}</p>
                      <Button variant="secondary" aria-pressed={view.fit === "width"} onClick={pick(() => setView((v) => ({ ...v, fit: "width" })))}>
                        {w("read.fit_width")}
                      </Button>
                      <Button variant="secondary" aria-pressed={view.fit === "page"} onClick={pick(() => setView((v) => ({ ...v, fit: "page" })))}>
                        {w("read.whole_page")}
                      </Button>
                    </div>
                    <div className="rd-more__group" role="group" aria-labelledby="rd-more-view">
                      <p id="rd-more-view" className="rd-more__head">{w("read.more.view")}</p>
                      {/* The sliders stay open beside the page, so this one leaves the panel open too. */}
                      <Button variant="secondary" aria-expanded={clearer} aria-controls="rd-clearer" onClick={() => setClearer((o) => !o)}>
                        {w("read.clearer_view")}
                      </Button>
                    {clearer && (
                      <div id="rd-clearer" className="rd-clearer">
                        {(["brightness", "contrast"] as const).map((k) => (
                          <label key={k} className="rd-clearer__row">
                            <span className="rd-label">{w(k === "brightness" ? "read.brightness" : "read.contrast")}</span>
                            <input type="range" min={50} max={200} step={5} value={view[k]} onChange={(e) => setView((v) => ({ ...v, [k]: Number(e.target.value) }))} />
                          </label>
                        ))}
                        <Button variant="secondary" onClick={() => setView((v) => ({ ...v, brightness: 100, contrast: 100 }))}>
                          {w("read.back_to_normal")}
                        </Button>
                      </div>
                    )}
                      <Button variant="secondary" aria-pressed={tools.twoPages} onClick={pick(() => setTools((t) => ({ ...t, twoPages: !t.twoPages })))}>
                        {w("read.two_pages")}
                      </Button>
                    </div>
                    <div className="rd-more__group" role="group" aria-labelledby="rd-more-page">
                      <p id="rd-more-page" className="rd-more__head">{w("read.more.page")}</p>
                      <Button variant="secondary" disabled={busy} onClick={pick(() => void askClearerCopy())}>
                        {w("read.clearer")}
                      </Button>
                      <LinkButton variant="secondary" to={`/cases/${caseId}/bundle/not-this-patient?page=${pageNo}`}>
                        {w("read.not_patient")}
                      </LinkButton>
                    </div>
                  </div>
                )}
              </span>
              <Button variant="secondary" className="rd-view__full" aria-pressed={tools.full} onClick={() => setFull(!tools.full)}>
                {w(tools.full ? "read.leave_full_screen" : "read.full_screen")}
              </Button>
            </div>
            {tools.twoPages && besideNo !== null && (
              <form
                className="rd-goto"
                onSubmit={(e) => {
                  e.preventDefault();
                  void showBeside();
                }}
              >
                <label htmlFor="rd-beside" className="rd-label">
                  {w("read.beside")}
                </label>
                <input
                  id="rd-beside"
                  className="av-input rd-goto__input"
                  type="text"
                  inputMode="numeric"
                  autoComplete="off"
                  value={besideText ?? String(besideNo)}
                  aria-describedby={besideError ? "rd-beside-error" : undefined}
                  onChange={(e) => {
                    setBesideText(e.target.value);
                    setBesideError(null);
                  }}
                />
                <Button type="submit" variant="secondary">
                  {w("read.beside_show")}
                </Button>
                {besideError && (
                  <p id="rd-beside-error" className="rd-goto__error" role="alert">
                    {besideError}
                  </p>
                )}
              </form>
            )}
            {findOpen && (
              <div id="rd-find">
                <BundleSearch caseId={caseId} pageNo={pageNo} result={found} onResult={showFound} onOpen={openPage} focus={focusFind} />
              </div>
            )}
          </div>
          <div className={tools.twoPages ? "rd-viewer rd-viewer--two" : "rd-viewer"}>
            {pages && <PageStrip caseId={caseId} pages={pages} current={pageNo} currentRead={read} onOpen={openPage} />}
            {moving?.words ? (
              <PageImage key={`move-${moving.page}`} caseId={caseId} pageNo={moving.page} view={view} filter={filter} onZoom={zoomBy} onScale={setScale}>
                <WordToggles
                  words={moving.words}
                  chosen={moving.chosen}
                  onToggle={(id) => setMoving((m) => m && { ...m, problem: null, chosen: m.chosen.includes(id) ? m.chosen.filter((x) => x !== id) : [...m.chosen, id] })}
                />
              </PageImage>
            ) : (
              <PageImage caseId={caseId} pageNo={pageNo} turn={turn} view={view} filter={filter} onZoom={zoomBy} onScale={setScale}>
                <Highlights facts={facts ?? []} focus={focus} />
                <SearchMarks result={found} pageNo={pageNo} />
                {editing && pageWords && <WordToggles words={pageWords} chosen={editing.words} onToggle={toggleWord} />}
              </PageImage>
            )}
            {tools.twoPages && besideNo !== null && !moving?.words && (
              <PageImage key={`beside-${besideNo}`} caseId={caseId} pageNo={besideNo} turn={0} view={view} filter={filter} onZoom={zoomBy} onScale={() => undefined}>
                <SearchMarks result={found} pageNo={besideNo} />
              </PageImage>
            )}
          </div>
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
          {facts && facts.length > 0 && <FactSwitcher pack={pack} facts={facts} focus={focus} onFocus={setFocus} />}
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
              formError={formError?.text ?? null}
              tickHint={formError?.tick === true}
              formAction={formError?.action ?? null}
              clearerPage={page.superseded_by_page_no ?? null}
              moving={moving?.id === focusedFact.id ? { page: moving.page, pageHref: `/cases/${caseId}/read/${moving.page}`, problem: moving.problem, readFirst: moving.readFirst } : null}
              onStartMove={page.superseded_by_page_no ? () => void startMove(focusedFact, page.superseded_by_page_no!) : undefined}
              onSaveMove={() => void saveMove(focusedFact)}
              onCancelMove={() => setMoving(null)}
              misread={editing?.id === focusedFact.id && editing.words.length > 0 ? { ticked: editing.misread, text: editing.misreadText } : null}
              onMisread={(ticked) => setEditing((e) => e && { ...e, misread: ticked })}
              onMisreadText={(text) => setEditing((e) => e && { ...e, misreadText: text })}
              typeOutHref={formError?.typeOut ? `/cases/${caseId}/read/couldnt-read?page=${pageNo}` : undefined}
              typedWords={editing?.id === focusedFact.id && pageWords ? <TypedWordToggles words={pageWords} chosen={editing.words} onToggle={toggleWord} /> : undefined}
              reason={reasons[focusedFact.id] ?? ""}
              onFocus={() => setFocus(focusedFact.id)}
              onReason={(text) => setReasons((r) => ({ ...r, [focusedFact.id]: text }))}
              onCorrect={() => act(focusedFact, "correct")}
              onNotRight={() => notRight(focusedFact)}
              onStartEdit={() => { setMoving(null); void startEdit(focusedFact); }}
              onEditText={(text) => setEditing((e) => e && { ...e, text })}
              onEditField={(key, value) =>
                setEditing((e) => {
                  if (!e) return e;
                  const fields = { ...e.fields, [key]: value };
                  const follow = e.eventDate !== null && !e.eventDateByHand && key === dateFieldKey(pack, focusedFact.kind);
                  return { ...e, fields, eventDate: follow ? suggestTimelineDate({ ...focusedFact, event_date: null, fields: { ...(focusedFact.fields ?? {}), ...fields } }, pack) : e.eventDate };
                })
              }
              editEventDate={editing?.id === focusedFact.id ? editing.eventDate : null}
              onEditEventDate={(value) => setEditing((e) => e && { ...e, eventDate: value, eventDateByHand: true })}
              onSaveEdit={() => saveEdit(focusedFact)}
              onCancelEdit={() => { setEditing(null); setFormError(null); }}
              onFlag={() => toggleFlag(focusedFact)}
              timelineDraft={timelineDraft?.id === focusedFact.id ? timelineDraft.value : null}
              onStartTimelineDate={() => {
                setEditing(null);
                setFormError(null);
                setTimelineDraft({ id: focusedFact.id, value: suggestTimelineDate(focusedFact, pack) });
              }}
              onTimelineDateChange={(value) => setTimelineDraft((d) => d && d.id === focusedFact.id ? { ...d, value } : d)}
              onSaveTimelineDate={() => {
                if (!timelineDraft || timelineDraft.id !== focusedFact.id || !timelineDraft.value) return;
                void act(focusedFact, "edit", { event_date: timelineDraft.value });
              }}
              onCancelTimelineDate={() => { setTimelineDraft(null); setFormError(null); }}
            />
          )}
          {canNote && <MyNotes caseId={caseId} pageNo={pageNo} />}
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
