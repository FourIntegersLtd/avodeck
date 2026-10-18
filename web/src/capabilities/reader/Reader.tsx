// Screen 09, Reading. The expert reads the page and marks it read; only then does the server release
// the suggestions (blind read, rule 3: the page asks for them after the read and shows none on a 423).
// Every action sends the version it loaded (If-Match). The server decides; this screen shows.
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, LoadError, Loading, Notice } from "../../foundation/ui";
import { casePath, sourceLabel } from "./data";
import { allowed, FactCard } from "./FactCard";
import { Highlights } from "./Highlights";
import { PageImage } from "./PageImage";
import { w } from "./words";

type Fact = Schemas["FactOut"];
type Action = "correct" | "not_right" | "edit";
const ZOOMS = [75, 100, 125, 150, 200, 250];

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
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [zoom, setZoom] = useState(100);

  const loadFacts = useCallback(async () => {
    try {
      const { facts: list } = await unwrap(api.GET("/v1/cases/{case_id}/pages/{page_no}/facts", { params: { path: { case_id: caseId, page_no: String(pageNo) } } }));
      // The server answers only once the page was read, so the page counts as read from here.
      setRead(true);
      setFacts(list);
      setFocus((f) => f ?? list.find((x) => x.status === "suggested")?.id ?? list[0]?.id ?? null);
    } catch (error) {
      if (error instanceof ApiError && error.status === 423) setFacts(null);
      else setNotice(errorMessage(error));
    }
  }, [caseId, pageNo]);

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

  function advanceFrom(list: Fact[], id: string) {
    const after = list.slice(list.findIndex((f) => f.id === id) + 1).find((f) => f.status === "suggested");
    setFocus(after?.id ?? id);
  }

  async function act(fact: Fact, action: Action, extra: { new_value?: string; word_ids?: string[]; reason?: string } = {}) {
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
      const list = (facts ?? []).map((f) => (f.id === next.id ? next : f));
      setFacts(list);
      setEditing(null);
      advanceFrom(list, next.id);
    } catch (error) {
      if (error instanceof ApiError && error.status === 412) {
        setNotice(w("read.error.stale"));
        await loadFacts();
      } else if (error instanceof ApiError && error.status === 422) {
        setNotice(w("read.error.not_in_page"));
      } else {
        setNotice(errorMessage(error, {}));
      }
    } finally {
      setBusy(false);
    }
  }

  const notRight = (f: Fact) => act(f, "not_right", reasons[f.id]?.trim() ? { reason: reasons[f.id]!.trim() } : {});
  const saveEdit = (f: Fact) => editing && editing.text.trim() && act(f, "edit", { new_value: editing.text.trim(), word_ids: f.word_ids });

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

  const nextPage = following.data ?? null;
  const go = (n: number) => n >= 1 && (n <= pageNo || n === nextPage) && navigate(`/cases/${caseId}/read/${n}`);
  const latest = useRef({ markRead, go, act, notRight, facts, focus, read, nextPage });
  latest.current = { markRead, go, act, notRight, facts, focus, read, nextPage };

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
      const s = latest.current;
      const key = e.key.toLowerCase();
      const target = s.facts?.find((f) => f.id === s.focus);
      if (key === "j") s.go(s.nextPage ?? 0);
      else if (key === "k") s.go(pageNo - 1);
      else if (key === "r") void s.markRead();
      else if (key === "1" && target && allowed(target).correct) void s.act(target, "correct");
      else if (key === "2" && target && allowed(target).notRight) void s.notRight(target);
      else if (key === "3" && target && allowed(target).edit) setEditing({ id: target.id, text: target.value ?? "" });
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
  const minutes = progress.data ? Math.ceil(progress.data.seconds_left / 60) : 0;
  const hard = queue.data ? queue.data.total - queue.data.done : 0;

  return (
    <div className="rd">
      {page.in_period && (
        <div className="rd-banner">
          <p>
            <strong>{w("read.in_period")}</strong> {read ? w("read.in_period_after") : ""}
          </p>
        </div>
      )}
      <div className="rd-work">
        <section className="rd-pane" aria-label={w("read.page_alt", { page: pageNo })}>
          <div className="rd-bar">
            <p className="rd-source">
              <strong>{sourceLabel(pack, page.source_type)}</strong> <span className="rd-ref">{w("read.page_ref", { page: pageNo })}</span>
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
          </div>
          <PageImage caseId={caseId} pageNo={pageNo} zoom={zoom}>
            <Highlights facts={facts ?? []} focus={focus} />
          </PageImage>
        </section>

        <aside className="rd-side" aria-label={w("read.found")}>
          <h2>{w("read.found")}</h2>
          {notice && <Notice tone="error">{notice}</Notice>}
          {facts === null && <p className="rd-note">{w("read.not_read_yet")}</p>}
          {facts?.length === 0 && <p className="rd-note">{w("read.none_found")}</p>}
          {facts?.map((f) => (
            <FactCard
              key={f.id}
              fact={f}
              pack={pack}
              focused={focus === f.id}
              busy={busy}
              editing={editing?.id === f.id ? editing.text : null}
              reason={reasons[f.id] ?? ""}
              onFocus={() => setFocus(f.id)}
              onReason={(text) => setReasons((r) => ({ ...r, [f.id]: text }))}
              onCorrect={() => act(f, "correct")}
              onNotRight={() => notRight(f)}
              onStartEdit={() => setEditing({ id: f.id, text: f.value ?? "" })}
              onEditText={(text) => setEditing({ id: f.id, text })}
              onSaveEdit={() => saveEdit(f)}
              onCancelEdit={() => setEditing(null)}
              onFlag={() => toggleFlag(f)}
            />
          ))}
          <div className="rd-keys">
            <p>
              <strong>{w("read.keys.heading")}</strong>
            </p>
            <p>{w("read.keys.help")}</p>
          </div>
        </aside>
      </div>

      <footer className="rd-foot">
        <Button variant="secondary" disabled={pageNo <= 1} onClick={() => go(pageNo - 1)}>
          {w("read.previous")}
        </Button>
        <div className="rd-progress">
          {progress.data && (
            <>
              <p>
                {left === 0
                  ? w("read.progress_done", { read: progress.data.read, total: progress.data.total })
                  : w("read.progress", { read: progress.data.read, total: progress.data.total, n: minutes, count: minutes })}
              </p>
              <progress max={progress.data.total || 1} value={progress.data.read} aria-label={w("read.progress_done", { read: progress.data.read, total: progress.data.total })} />
            </>
          )}
        </div>
        {hard > 0 && (
          <Link className="rd-hard" to={`/cases/${caseId}/read/couldnt-read`}>
            {w("read.couldnt_read", { count: hard })}
          </Link>
        )}
        {read ? <Button disabled={nextPage === null} onClick={() => nextPage !== null && go(nextPage)}>{w("read.next")}</Button> : <Button disabled={busy} onClick={() => void markRead()}>{w("read.mark_read")}</Button>}
      </footer>
    </div>
  );
}
