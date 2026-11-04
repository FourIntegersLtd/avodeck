// Screen 10, another reading of a page (T-10). The server hides the names on the picture before a model sees it
// and keeps its lines apart from the expert's own words; here they are only shown to check against the page.
import { useCallback, useEffect, useRef, useState } from "react";
import { useOnCaseChange } from "../../foundation/lib/sse";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Notice, SectionIcon } from "../../foundation/ui";
import { w } from "./words";

type Reading = Schemas["SecondReadingOut"];

export function SecondReading({ caseId, pageNo }: { caseId: string; pageNo: number }) {
  const [reading, setReading] = useState<Reading | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const path = { params: { path: { case_id: caseId, page_no: pageNo } } };

  // The page's last reading, if any.
  const current = useRef(`${caseId}/${pageNo}`);
  current.current = `${caseId}/${pageNo}`;
  const look = useCallback(async () => {
    const asked = `${caseId}/${pageNo}`;
    try {
      const got = await unwrap(api.GET("/v1/cases/{case_id}/pages/{page_no}/second-reading", { params: { path: { case_id: caseId, page_no: pageNo } } }));
      if (current.current === asked) setReading(got); // a late answer for a page we have left is dropped
    } catch {
      /* nothing to show yet */
    }
  }, [caseId, pageNo]);
  useEffect(() => {
    setReading(null);
    setFailed(false);
    void look();
  }, [look, busy]);
  // While a reading is waiting, the case's live stream says when to look again.
  useOnCaseChange(reading?.status === "queued" ? caseId : null, () => void look());

  async function ask() {
    setBusy(true);
    setFailed(false);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/pages/{page_no}/second-reading", { ...path, params: { ...path.params, query: { on_request: true } } }));
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  const waiting = busy || reading?.status === "queued";
  return (
    <div className="rd-second">
      {reading?.status === "done" && (
        <div className="av-card">
          <SectionIcon glyph="book" />
          <h2 className="rd-label">{w("second.title")}</h2>
          <ol className="rd-second__lines">
            {reading.lines.map((line, i) => (
              <li key={i}>{line.text}</li>
            ))}
          </ol>
        </div>
      )}
      {reading?.status === "refused" && (
        <Notice tone="amber">
          <p>{w("second.refused")}</p>
        </Notice>
      )}
      {(reading?.status === "failed" || failed) && (
        <Notice tone="error">
          <p>{w("second.failed")}</p>
        </Notice>
      )}
      {waiting && <p className="rd-note">{w("second.waiting")}</p>}
      <Button variant="secondary" disabled={waiting} onClick={() => void ask()}>
        {w("second.ask")}
      </Button>
    </div>
  );
}
