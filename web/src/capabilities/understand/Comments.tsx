// Comments on a fact (or page or paragraph): working notes for the case team. Never in a report, but they may be
// disclosable, so the screen says so before anyone writes one.
import { useState } from "react";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, ifMatch, unwrap } from "../../foundation/platform-client";
import { Button, Notice } from "../../foundation/ui";
import { casePath, TextArea } from "./shared";
import { w } from "./words";

export function Comments({ caseId, targetKind, targetId }: { caseId: string; targetKind: "page" | "fact" | "paragraph"; targetId: string }) {
  const found = useResource(
    () => unwrap(api.GET("/v1/cases/{case_id}/comments", { params: { path: { case_id: caseId }, query: { target_kind: targetKind, target_id: targetId } } })),
    [caseId, targetKind, targetId],
  );
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);

  const add = async () => {
    setError(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/comments", { ...casePath(caseId), body: { target_kind: targetKind, target_id: targetId, body: body.trim() } }));
      setBody("");
      found.reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  const resolve = async (id: string, version: number) => {
    setError(null);
    try {
      await unwrap(
        api.PATCH("/v1/cases/{case_id}/comments/{comment_id}", { params: { path: { case_id: caseId, comment_id: id } }, headers: ifMatch(`"${version}"`), body: { resolved: true } }),
      );
      found.reload();
    } catch (err) {
      setError(errorMessage(err, { stale: "understand.common.stale" }));
      found.reload();
    }
  };

  return (
    <section aria-labelledby="understand-comments" className="av-card av-stack">
      <h2 id="understand-comments">{w("comments.title")}</h2>
      <Notice tone="warning">
        <p>{w("comments.warning")}</p>
      </Notice>
      {found.data && found.data.comments.length === 0 && <p className="av-muted">{w("comments.none")}</p>}
      <ul className="av-understand-list">
        {(found.data?.comments ?? []).map((c) => (
          <li key={c.id} className="av-understand-event">
            <span>{c.body}</span>
            {c.resolved_at ? (
              <span className="av-muted">{w("comments.resolved")}</span>
            ) : (
              <Button variant="secondary" onClick={() => void resolve(c.id, c.version)}>
                {w("comments.resolve")}
              </Button>
            )}
          </li>
        ))}
      </ul>
      <TextArea label={w("comments.add")} rows={3} value={body} onChange={(e) => setBody(e.target.value)} />
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      <Button disabled={!body.trim()} onClick={() => void add()}>
        {w("comments.add_button")}
      </Button>
    </section>
  );
}
