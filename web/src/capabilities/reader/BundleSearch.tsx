// Find words in the bundle (screen 09). The server searches only the words read from the pages, never
// the suggestions, and answers page numbers and places; searching is not reading (rule 3).
import { useState } from "react";
import { Link } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { api, unwrap } from "../../foundation/platform-client";
import { Button } from "../../foundation/ui";
import { pct } from "./data";
import { searchable, type SearchResult } from "./search";
import { w } from "./words";

interface Props {
  caseId: string;
  result: SearchResult | null;
  onResult: (result: SearchResult | null) => void;
}

export function BundleSearch({ caseId, result, onResult }: Props) {
  const [q, setQ] = useState(result?.q ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function find() {
    if (!searchable(q)) return setProblem(w("read.search.too_short"));
    setProblem(null);
    setBusy(true);
    try {
      const out = await unwrap(api.POST("/v1/cases/{case_id}/search", { params: { path: { case_id: caseId } }, body: { q } }));
      onResult({ q, pages: out.pages, total: out.total_hits });
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
        <Button type="submit" variant="secondary" disabled={busy}>
          {w("read.search.find")}
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
        <div className="rd-search__found">
          <p role="status">{result.pages.length === 0 ? w("read.search.none") : w("read.search.found", { count: result.pages.length })}</p>
          {result.pages.length > 0 && (
            <ul className="rd-search__list" aria-label={w("read.search.list")}>
              {result.pages.map((p) => (
                <li key={p.page_no}>
                  <Link className="rd-search__page" to={`/cases/${caseId}/read/${p.page_no}`}>
                    {w("read.search.page", { page: p.page_no, count: p.hits })}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
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
