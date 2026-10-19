// Shared by screens 14 and 15: the questions the expert can answer (confirmed or edited ones only), the one
// chosen with ?q=, and small helpers for versions.
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Dialog, LinkButton, LoadError, Loading, Notice, TextField } from "../../foundation/ui";
import { w } from "./words";

export const versionTag = (version: number) => `"${version}"`;

export type Answerable = Schemas["QuestionOut"];

export interface Chosen {
  all: Answerable[];
  current: Answerable;
  next: Answerable | undefined;
  /** Where the expert has got to; null while loading or if it could not be read (the screens work without it). */
  progress: Schemas["WriteProgress"] | null;
}

/** Loads the answerable questions and hands the chosen one to `children`; shows why when there is none. */
export function WithQuestion({ caseId, children }: { caseId: string; children: (chosen: Chosen) => React.ReactNode }) {
  const [params] = useSearchParams();
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/questions", { params: { path: { case_id: caseId } } })), [caseId]);
  const progress = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/write-progress", { params: { path: { case_id: caseId } } })), [caseId]);
  if (found.error) return <div className="av-page"><LoadError onRetry={found.reload} /></div>;
  if (!found.data) return <div className="av-page"><Loading /></div>;
  const all = found.data.questions.filter((q) => q.status === "confirmed" || q.status === "edited").sort((a, b) => a.number - b.number);
  const index = Math.max(0, all.findIndex((q) => q.id === params.get("q")));
  const current = all[index];
  if (!current) {
    return (
      <div className="av-page av-page--narrow av-stack">
        <Notice>
          <p>{w("nothing_to_answer")}</p>
        </Notice>
        <LinkButton to={`/cases/${caseId}/questions`} variant="secondary">
          {w("open_questions")}
        </LinkButton>
      </div>
    );
  }
  return <>{children({ all, current, next: all[index + 1], progress: progress.data ?? null })}</>;
}

export function QuestionTabs({ caseId, chosen, screen }: { caseId: string; chosen: Chosen; screen: "points" | "opinion" }) {
  return (
    <nav className="av-write-tabs" aria-label={w("points.title")}>
      {chosen.all.map((q) => (
        <Link key={q.id} className="av-write-tab" to={`/cases/${caseId}/${screen}?q=${q.id}`} aria-current={q.id === chosen.current.id ? "page" : undefined}>
          {chosen.progress?.questions.find((p) => p.question_id === q.id)?.points_done && (
            <>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M5 12l5 5 9-10" />
              </svg>
              <span className="av-visually-hidden">{w("points.tab_done")} </span>
            </>
          )}
          {w("tab.question", { number: q.number })}
        </Link>
      ))}
    </nav>
  );
}

export function pageLabel(page: number): string {
  return w("points.page", { page });
}

/** A short question with one line to answer: why a point is removed or a check is dismissed. */
export function ReasonDialog(props: { title: string; label: string; confirm: string; onConfirm: (reason: string) => void; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const [needed, setNeeded] = useState(false);
  return (
    <Dialog title={props.title} onClose={props.onClose}>
      <form
        className="av-stack"
        onSubmit={(e) => {
          e.preventDefault();
          if (!reason.trim()) return setNeeded(true);
          props.onConfirm(reason.trim());
        }}
      >
        <TextField label={props.label} name="reason" value={reason} maxLength={200} error={needed ? w("points.reason_needed") : undefined} onChange={(e) => setReason(e.target.value)} />
        <div className="av-write-actions">
          <Button type="submit">{props.confirm}</Button>
          <Button variant="secondary" onClick={props.onClose}>
            {w("points.cancel")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
