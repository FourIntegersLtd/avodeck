// Find words in the bundle (screen 09). The server searches only the words read from the pages, never
// the suggestions, and answers page numbers and places; searching is not reading (rule 3).
// Shown when the expert opens "Find words" on the page toolbar. A search opens the first page with the
// words (unless this page has them), and Previous and Next step through the pages found.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { api, unwrap } from "../../foundation/platform-client";
import { Button } from "../../foundation/ui";
import { pct } from "./data";
import { searchable, type SearchResult } from "./search";
import { w } from "./words";

interface Props {
  caseId: string;
  /** The page on show. */
  pageNo: number;
  result: SearchResult | null;
  onResult: (result: SearchResult | null) => void;
  /** Opens a page of the bundle. */
  onOpen: (pageNo: number) => void;
  /** Put the cursor in the box (the expert has just asked to find words). */
  focus?: boolean;
}

export function BundleSearch({ caseId, pageNo, result, onResult, onOpen, focus }: Props) {
  const [q, setQ] = useState(result?.q ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (focus) input.current?.focus();
  }, [focus]);
  const found = result?.pages ?? [];
  const previous = [...found].reverse().find((p) => p.page_no < pageNo)?.page_no ?? null;
  const next = found.find((p) => p.page_no > pageNo)?.page_no ?? null;

  async function find() {
    if (!searchable(q)) return setProblem(w("read.search.too_short"));
    setProblem(null);
    setBusy(true);
    try {
      const out = await unwrap(api.POST("/v1/cases/{case_id}/search", { params: { path: { case_id: caseId } }, body: { q } }));
      onResult({ q, pages: out.pages, total: out.total_hits });
      // Straight to the words: the first page with them, unless they are on this page.
      const first = out.pages[0];
      if (first && !out.pages.some((p) => p.page_no === pageNo)) onOpen(first.page_no);
    } catch (error) {
      setProblem(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rd-search">
      <form
        role="search"
        aria-label={w("read.search.label")}
        className="rd-search__form"
        onSubmit={(e) => {
          e.preventDefault();
          void find();
        }}
      >
        <label htmlFor="rd-search" className="rd-label">
          {w("read.search.label")}
        </label>
        <input
          id="rd-search"
          ref={input}
          className="av-input rd-search__input"
          type="search"
          autoComplete="off"
          maxLength={100}
          value={q}
          aria-describedby={problem ? "rd-search-problem" : undefined}
          onChange={(e) => {
            setQ(e.target.value);
            setProblem(null);
          }}
        />
        <Button type="submit" variant="secondary" disabled={busy} aria-busy={busy}>
          {busy ? w("read.search.finding") : w("read.search.find")}
        </Button>
        {result && (
          <Button
            variant="secondary"
            onClick={() => {
              onResult(null);
              setQ("");
            }}
          >
            {w("read.search.clear")}
          </Button>
        )}
      </form>
      {problem && (
        <p id="rd-search-problem" className="rd-goto__error" role="alert">
          {problem}
        </p>
      )}
      {result && (
        <section className="rd-search__found" aria-label={w("read.search.list")}>
          <div className="rd-search__head">
            <p role="status">{found.length === 0 ? w("read.search.none") : w("read.search.found", { count: found.length })}</p>
            {found.length > 0 && (
              <span className="rd-search__steps">
                <Button variant="secondary" aria-label={w("read.search.previous")} disabled={previous === null} onClick={() => previous !== null && onOpen(previous)}>
                  {w("read.search.previous_short")}
                </Button>
                <Button variant="secondary" aria-label={w("read.search.next")} disabled={next === null} onClick={() => next !== null && onOpen(next)}>
                  {w("read.search.next_short")}
                </Button>
              </span>
            )}
          </div>
          {found.length > 0 && (
            <ul className="rd-search__list" aria-label={w("read.search.list")}>
              {found.map((p) => (
                <li key={p.page_no}>
                  <Link
                    className={p.page_no === pageNo ? "rd-search__page rd-search__page--on" : "rd-search__page"}
                    aria-current={p.page_no === pageNo ? "page" : undefined}
                    to={`/cases/${caseId}/read/${p.page_no}`}
                  >
                    {w("read.search.page", { page: p.page_no, count: p.hits })}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
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
