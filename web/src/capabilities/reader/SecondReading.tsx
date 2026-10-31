// Screen 10, another reading of a page (T-10). The server hides the names on the picture before a model sees it
// and keeps its lines apart from the expert's own words; here they are only shown to check against the page.
import { useEffect, useState } from "react";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Notice, SectionIcon } from "../../foundation/ui";
import { w } from "./words";

type Reading = Schemas["SecondReadingOut"];
const POLL_MS = 2000;

export function SecondReading({ caseId, pageNo }: { caseId: string; pageNo: number }) {
  const [reading, setReading] = useState<Reading | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const path = { params: { path: { case_id: caseId, page_no: pageNo } } };

  // The page's last reading, if any; while one is waiting, look again every couple of seconds.
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const look = async () => {
      try {
        const got = await unwrap(api.GET("/v1/cases/{case_id}/pages/{page_no}/second-reading", path));
        if (!live) return;
        setReading(got);
        if (got.status === "queued") timer = setTimeout(() => void look(), POLL_MS);
      } catch {
        /* nothing to show yet */
      }
    };
    setReading(null);
    setFailed(false);
    void look();
    return () => {
      live = false;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId, pageNo, busy]);

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
