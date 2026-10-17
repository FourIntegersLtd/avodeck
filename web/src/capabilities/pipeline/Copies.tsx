// Screen 08, the copies set aside: each copy beside its original. The expert confirms a copy or keeps
// the page; every change sends the version it loaded, and a 412 shows the latest instead.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, LoadError, Loading, Notice } from "../../foundation/ui";
import { can, casePath } from "./data";
import { formatNumber } from "./format";
import { Thumbnail } from "./Thumbnail";
import { w } from "./words";

type Copy = Schemas["DuplicatePage"];
type Status = "suggested" | "confirmed" | "kept";

export function Copies() {
  const { item, pack } = useCaseFrame();
  const caseId = item.id;
  const list = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/duplicates", casePath(caseId))), [caseId]);
  const [copies, setCopies] = useState<Copy[] | null>(null);
  const [notice, setNotice] = useState<{ tone: "info" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const reviews = can(item, "page.review_duplicate");
  const num = (n: number) => formatNumber(n, pack.locale);

  useEffect(() => {
    if (list.data) setCopies(list.data.duplicates);
  }, [list.data]);

  async function review(copy: Copy, action: "confirm" | "undo") {
    setBusy(copy.page_id);
    setNotice(null);
    try {
      const changed = await unwrap(
        api.PATCH("/v1/cases/{case_id}/pages/{page_id}/duplicate", {
          params: { path: { case_id: caseId, page_id: copy.page_id } },
          headers: ifMatch(`"${copy.version}"`),
          body: { action },
        }),
      );
      setCopies((all) => (all ?? []).map((c) => (c.page_id === changed.page_id ? changed : c)));
    } catch (error) {
      if (error instanceof ApiError && error.status === 412) {
        setNotice({ tone: "info", text: w("copies.changed") });
        list.reload();
      } else {
        setNotice({ tone: "error", text: errorMessage(error) });
      }
    } finally {
      setBusy(null);
    }
  }

  if (list.error) return <div className="av-page"><LoadError onRetry={list.reload} /></div>;
  if (!copies) return <div className="av-page"><Loading /></div>;

  return (
    <div className="av-page av-stack">
      <Link className="pl-back" to={`/cases/${caseId}/bundle`}>{w("back")}</Link>
      <h1>{w("copies.title")}</h1>
      <p className="pl-lead">{w("copies.intro")}</p>
      {notice && <Notice tone={notice.tone}><p>{notice.text}</p></Notice>}
      {copies.length === 0 ? (
        <p>{w("copies.none")}</p>
      ) : (
        <ul className="pl-plain pl-list">
          {copies.map((c) => {
            const status = c.status as Status;
            return (
              <li key={c.page_id} className="av-card pl-copy">
                <div className="pl-copy__pages">
                  <Thumbnail caseId={caseId} pageNo={c.page_no} alt={w("page_alt", { page: num(c.page_no) })} />
                  {c.copy_of_page_no != null && <Thumbnail caseId={caseId} pageNo={c.copy_of_page_no} alt={w("page_alt", { page: num(c.copy_of_page_no) })} />}
                </div>
                <div className="pl-copy__body">
                  <h2 className="pl-card__title">{w("copies.pair", { page: num(c.page_no), original: num(c.copy_of_page_no ?? 0) })}</h2>
                  <span className={`av-badge pl-badge--${status}`}>{w(`copies.status.${status}`)}</span>
                  {reviews && status !== "kept" && (
                    <div className="av-row">
                      {status === "suggested" && (
                        <Button variant="secondary" onClick={() => review(c, "confirm")} disabled={busy === c.page_id}>
                          {w("copies.confirm")}
                        </Button>
                      )}
                      <Button variant="quiet" onClick={() => review(c, "undo")} disabled={busy === c.page_id}>
                        {w("copies.keep")}
                      </Button>
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
