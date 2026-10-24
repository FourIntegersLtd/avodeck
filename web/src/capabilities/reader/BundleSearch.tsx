// Find words in the bundle (screen 09): one slim row. The server searches only the words read from the
// pages, never the suggestions, and answers page numbers and places; searching is not reading (rule 3).
// It searches as you type (after a short pause), opens the first page with the words (unless this page
// has them), and Enter, the arrows and "Show all" move between the pages found.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { api, unwrap } from "../../foundation/platform-client";
import { Button } from "../../foundation/ui";
import { pct } from "./data";
import { ChevronIcon, CloseIcon, FindIcon } from "./icons";
import { pageImage } from "./pageImageCache";
import { searchable, type SearchResult } from "./search";
import { w } from "./words";

const PAUSE_MS = 300;

interface Props {
  caseId: string;
  /** The page on show. */
  pageNo: number;
  result: SearchResult | null;
  onResult: (result: SearchResult | null) => void;
  /** Opens a page of the bundle. */
  onOpen: (pageNo: number) => void;
  /** Closes the bar. */
  onDone: () => void;
  /** Put the cursor in the box (the expert has just asked to find words). */
  focus?: boolean;
}

export function BundleSearch({ caseId, pageNo, result, onResult, onOpen, onDone, focus }: Props) {
  const [q, setQ] = useState(result?.q ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [all, setAll] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // Only the newest question may answer: a slow old answer is dropped.
  const seq = useRef(0);
  const here = useRef({ pageNo, onOpen, onResult });
  here.current = { pageNo, onOpen, onResult };
  useEffect(() => {
    if (focus) input.current?.focus();
  }, [focus]);
  useEffect(() => () => clearTimeout(timer.current), []);

  const found = result?.pages ?? [];
  const at = found.findIndex((p) => p.page_no === pageNo);
  const previous = [...found].reverse().find((p) => p.page_no < pageNo)?.page_no ?? null;
  const next = found.find((p) => p.page_no > pageNo)?.page_no ?? null;

  async function run(text: string) {
    const mine = ++seq.current;
    if (text.trim() === "") {
      setProblem(null);
      setBusy(false);
      here.current.onResult(null);
      return;
    }
    if (!searchable(text)) {
      setBusy(false);
      here.current.onResult(null);
      return setProblem(w("read.search.too_short"));
    }
    setProblem(null);
    setBusy(true);
    try {
      const out = await unwrap(api.POST("/v1/cases/{case_id}/search", { params: { path: { case_id: caseId } }, body: { q: text } }));
      if (mine !== seq.current) return;
      here.current.onResult({ q: text, pages: out.pages, total: out.total_hits });
      // Straight to the words: the first page with them, unless they are on this page.
      const first = out.pages[0];
      if (first && !out.pages.some((p) => p.page_no === here.current.pageNo)) {
        // Fetch the picture first so the page opens with its image, not an empty frame.
        await pageImage(caseId, first.page_no).catch(() => undefined);
        if (mine === seq.current) here.current.onOpen(first.page_no);
      }
    } catch (error) {
      if (mine === seq.current) setProblem(errorMessage(error));
    } finally {
      if (mine === seq.current) setBusy(false);
    }
  }

  function change(text: string) {
    setQ(text);
    setProblem(null);
    setAll(false);
    seq.current++;
    clearTimeout(timer.current);
    timer.current = setTimeout(() => void run(text), PAUSE_MS);
  }

  function enter() {
    clearTimeout(timer.current);
    if (result && result.q === q && !busy) {
      if (next !== null) onOpen(next);
    } else void run(q);
  }

  const status = problem ? null : busy ? w("read.search.finding") : result && found.length === 0 ? w("read.search.none") : null;
  return (
    <div className="rd-find" role="search" aria-label={w("read.search.label")}>
      <div className="rd-find__field">
        <FindIcon />
        <input
          ref={input}
          className="rd-find__input"
          type="search"
          autoComplete="off"
          maxLength={100}
          aria-label={w("read.search.label")}
          value={q}
          onChange={(e) => change(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              enter();
            }
          }}
        />
        {q !== "" && (
          <button
            type="button"
            className="rd-find__clear"
            aria-label={w("read.search.clear")}
            onClick={() => {
              clearTimeout(timer.current);
              setQ("");
              setAll(false);
              void run("");
              input.current?.focus();
            }}
          >
            <CloseIcon />
          </button>
        )}
      </div>
      <span className="rd-find__count" role="status" aria-live="polite">
        {status ?? (found.length > 0 && at >= 0 ? w("read.search.position", { n: at + 1, total: found.length }) : "")}
      </span>
      {problem && (
        <span className="rd-find__problem" role="alert">
          {problem}
        </span>
      )}
      {found.length > 0 && (
        <>
          <span className="rd-find__steps">
            <button type="button" className="rd-find__step" aria-label={w("read.search.previous")} disabled={previous === null} onClick={() => previous !== null && onOpen(previous)}>
              <ChevronIcon dir="left" />
            </button>
            <button type="button" className="rd-find__step" aria-label={w("read.search.next")} disabled={next === null} onClick={() => next !== null && onOpen(next)}>
              <ChevronIcon dir="right" />
            </button>
          </span>
          <span className="rd-find__all">
            <Button variant="quiet" aria-expanded={all} aria-controls="rd-find-all" onClick={() => setAll((o) => !o)}>
              {w("read.search.show_all")}
            </Button>
            {all && (
              <ul id="rd-find-all" className="rd-find__list" aria-label={w("read.search.list")}>
                {found.map((p) => (
                  <li key={p.page_no}>
                    <Link
                      className={p.page_no === pageNo ? "rd-find__page rd-find__page--on" : "rd-find__page"}
                      aria-current={p.page_no === pageNo ? "page" : undefined}
                      to={`/cases/${caseId}/read/${p.page_no}`}
                      onClick={() => setAll(false)}
                    >
                      {w("read.search.page", { page: p.page_no, count: p.hits })}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </span>
        </>
      )}
      <Button variant="quiet" className="rd-find__done" onClick={onDone}>
        {w("read.done")}
      </Button>
    </div>
  );
}

/** The places of the search's hits on one page, over its picture. */
export function SearchMarks({ result, pageNo }: { result: SearchResult | null; pageNo: number }) {
  const boxes = result?.pages.find((p) => p.page_no === pageNo)?.boxes ?? [];
  return (
    <>
      {boxes.map((b, i) => (
        <span
          key={i}
          data-testid={`search-hit-${i}`}
          className="rd-hit"
          style={{ left: pct(b.x0), top: pct(b.y0), width: pct(b.x1 - b.x0), height: pct(b.y1 - b.y0) }}
          aria-hidden="true"
        />
      ))}
    </>
  );
}
