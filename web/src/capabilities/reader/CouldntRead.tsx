// Screen 10, Pages we couldn't read. The expert types out each page the machine could not read, says
// whether it matters, and moves on. The server stores the typing and decides what follows.
import { useEffect, useId, useState } from "react";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorMessage } from "../../foundation/lib/errors";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { BackLink, Button, ChoicePills, DictationBar, LoadError, Loading, Notice, ProgressBar } from "../../foundation/ui";
import { casePath, count, sourceLabel } from "./data";
import { PageImage } from "./PageImage";
import { forgetPage } from "./pageImage";
import { uploadLaterRecords } from "./upload";
import { w } from "./words";

type Queue = Schemas["UnreadableQueue"];
type Relevance = "relevant" | "background";

export function CouldntRead() {
  const { item, pack } = useCaseFrame();
  const caseId = item.id;
  const [queue, setQueue] = useState<Queue | null>(null);
  const [failed, setFailed] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [relevance, setRelevance] = useState<Relevance | null>(null);
  const [busy, setBusy] = useState(false);
  const [copying, setCopying] = useState(false);
  const [copyOf, setCopyOf] = useState("");
  const fileId = useId();
  const [notice, setNotice] = useState<{ tone: "error" | "success"; text: string } | null>(null);

  const fetchQueue = () => unwrap(api.GET("/v1/cases/{case_id}/pages/unreadable", casePath(caseId)));

  function choose(page: Schemas["UnreadablePage"] | undefined) {
    setSelected(page?.page_no ?? null);
    setText("");
    setRelevance((page?.relevance as Relevance | null) ?? null);
    setNotice(null);
    setCopying(false);
    setCopyOf("");
  }

  useEffect(() => {
    let live = true;
    fetchQueue().then(
      (q) => {
        if (!live) return;
        setQueue(q);
        choose(q.pages.find((p) => !p.done));
      },
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId]);

  if (failed) return <LoadError onRetry={() => window.location.reload()} />;
  if (!queue) return <Loading />;
  const current = queue.pages.find((p) => p.page_no === selected);

  async function saveAndNext() {
    if (!current || busy || !text.trim()) return;
    setBusy(true);
    setNotice(null);
    try {
      await unwrap(
        api.PUT("/v1/cases/{case_id}/pages/{page_no}/transcription", {
          params: { path: { case_id: caseId, page_no: current.page_no } },
          body: { text: text.trim(), ...(relevance ? { relevance } : {}) },
        }),
      );
      const fresh = await fetchQueue();
      setQueue(fresh);
      const at = fresh.pages.findIndex((p) => p.page_no === current.page_no);
      choose(fresh.pages.slice(at + 1).find((p) => !p.done) ?? fresh.pages.find((p) => !p.done));
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error, { unavailable: "reader.unreadable.error.unavailable" }) });
    } finally {
      setBusy(false);
    }
  }

  async function askForClearerCopy() {
    if (!current || busy) return;
    setBusy(true);
    setNotice(null);
    try {
      await unwrap(
        api.PATCH("/v1/cases/{case_id}/pages/{page_no}", {
          params: { path: { case_id: caseId, page_no: current.page_no } },
          body: { needs_clearer_copy: true },
        }),
      );
      setQueue((q) => q && { ...q, pages: q.pages.map((p) => (p.page_no === current.page_no ? { ...p, needs_clearer_copy: true } : p)) });
      setNotice({ tone: "success", text: w("unreadable.clearer_asked") });
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  // Runs one action on this page, then moves to the next page still to do.
  async function settle(run: () => Promise<void>, failure: string) {
    if (!current || busy) return;
    setBusy(true);
    setNotice(null);
    try {
      await run();
      const fresh = await fetchQueue();
      setQueue(fresh);
      const at = fresh.pages.findIndex((p) => p.page_no === current.page_no);
      choose(fresh.pages.slice(at + 1).find((p) => !p.done) ?? fresh.pages.find((p) => !p.done));
    } catch (error) {
      setNotice({ tone: "error", text: failure || errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  const nothingRelevant = () =>
    settle(async () => {
      await unwrap(api.PATCH("/v1/cases/{case_id}/pages/{page_no}", { params: { path: { case_id: caseId, page_no: current!.page_no } }, body: { relevance: "nothing" } }));
    }, "");

  const setAsideAsCopy = () =>
    settle(async () => {
      await unwrap(
        api.PUT("/v1/cases/{case_id}/pages/{page_id}/duplicate", {
          params: { path: { case_id: caseId, page_id: current!.page_id } },
          body: { copy_of_page_no: Number(copyOf) },
        }),
      );
    }, w("unreadable.copy_error"));

  const addClearerCopy = (file: File) =>
    settle(async () => {
      const document_id = await uploadLaterRecords(caseId, file);
      await unwrap(api.POST("/v1/cases/{case_id}/documents/append", { params: { path: { case_id: caseId } }, body: { document_ids: [document_id] } }));
      await unwrap(api.POST("/v1/cases/{case_id}/pages/{page_id}/replace", { params: { path: { case_id: caseId, page_id: current!.page_id } }, body: { document_id } }));
      forgetPage(caseId, current!.page_no);
    }, w("unreadable.add_error"));

  return (
    <div className="rd rd--narrow">
      <BackLink to={`/cases/${caseId}/read`}>{w("unreadable.back")}</BackLink>
      <div className="rd-head">
        <div>
          <h1>{w("unreadable.title")}</h1>
          <p className="rd-lead">{w("unreadable.lead")}</p>
        </div>
        <div className="rd-progress">
          <p>
            <strong>{w("unreadable.done", { done: count(queue.done, pack.locale), total: count(queue.total, pack.locale) })}</strong>
          </p>
          <ProgressBar max={queue.total || 1} value={queue.done} label={w("unreadable.done", { done: count(queue.done, pack.locale), total: count(queue.total, pack.locale) })} />
        </div>
      </div>

      {!current ? (
        <Notice tone="success">
          <p>{w("unreadable.all_done")}</p>
        </Notice>
      ) : (
        <div className="rd-work">
          <section className="rd-pane" aria-label={w("read.page_alt", { page: current.page_no })}>
            <p className="rd-source">
              {current.handwriting && <span className="rd-tag">{w("unreadable.handwriting")}</span>}{" "}
              {sourceLabel(pack, current.source_type)} <span className="rd-ref">{w("read.page_ref", { page: current.page_no })}</span>
            </p>
            <PageImage caseId={caseId} pageNo={current.page_no} />
          </section>

          <section className="rd-side">
            <div className="av-card rd-form">
              <DictationBar caseId={caseId} context="page_transcription" label={w("unreadable.text_label")} value={text} onChange={setText} />
              <ChoicePills
                label={w("unreadable.relevance")}
                name="relevance"
                value={relevance}
                onChange={(v) => setRelevance(v as Relevance)}
                options={(["relevant", "background"] as const).map((r) => ({ value: r, label: w(`unreadable.${r}`) }))}
              />
            </div>
            {notice && <Notice tone={notice.tone}>{notice.text}</Notice>}
            {current.needs_clearer_copy && !notice && <p className="rd-note">{w("unreadable.clearer_asked")}</p>}
            <p className="rd-label rd-label--small">{w("unreadable.choose")}</p>
            <div className="rd-choices">
              <Button variant="secondary" disabled={busy} onClick={() => void nothingRelevant()}>
                {w("unreadable.nothing")}
              </Button>
              <Button variant="secondary" disabled={busy} aria-expanded={copying} onClick={() => setCopying((v) => !v)}>
                {w("unreadable.is_copy")}
              </Button>
              <Button variant="secondary" disabled={busy || current.needs_clearer_copy} onClick={() => void askForClearerCopy()}>
                {w("unreadable.clearer")}
              </Button>
              <label htmlFor={fileId} className="av-button av-button--secondary">
                {w("unreadable.add_clearer")}
              </label>
              <input
                id={fileId}
                type="file"
                accept="application/pdf,image/*"
                className="av-visually-hidden"
                disabled={busy}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (file) void addClearerCopy(file);
                }}
              />
            </div>
            {copying && (
              <div className="rd-copy">
                <label className="rd-label" htmlFor={`${fileId}-n`}>
                  {w("unreadable.copy_of")}
                </label>
                <input id={`${fileId}-n`} className="av-input" inputMode="numeric" value={copyOf} onChange={(e) => setCopyOf(e.target.value.replace(/\D/g, ""))} />
                <Button disabled={busy || !copyOf} onClick={() => void setAsideAsCopy()}>
                  {w("unreadable.copy_set")}
                </Button>
              </div>
            )}
            <Button wide arrow disabled={busy || !text.trim()} onClick={() => void saveAndNext()}>
              {w("unreadable.save_next")}
            </Button>
          </section>
        </div>
      )}

      <details className="rd-pages">
        <summary>{w("unreadable.all_pages")}</summary>
      <nav aria-label={w("unreadable.queue")}>
        <ul className="rd-queue">
          {queue.pages.map((p) => (
            <li key={p.page_id}>
              <button type="button" className="rd-queue__item" aria-current={p.page_no === selected ? "true" : undefined} onClick={() => choose(p)}>
                <span className="rd-ref">{w("unreadable.queue_item", { page: p.page_no })}</span> {sourceLabel(pack, p.source_type)}
                {p.done && <span aria-hidden="true"> ✓</span>}
                {p.done && <span className="rd-sr"> {w("unreadable.queue_done")}</span>}
              </button>
            </li>
          ))}
        </ul>
      </nav>
      </details>
    </div>
  );
}
