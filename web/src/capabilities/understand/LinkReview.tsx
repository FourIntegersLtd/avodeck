// S13 links between checked entries and the questions asked. The AI suggests (T-11); the named expert confirms
// or rejects each one, or adds one by hand to a confirmed question. A link whose question was taken away, or
// whose entry was marked not right, no longer holds: it stays listed apart and cannot be confirmed.
import { useEffect, useState } from "react";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { ApiError, api, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { screenPath } from "../../foundation/lib/screens";
import { Button, LinkButton, Notice, useBusy } from "../../foundation/ui";
import { casePath, longDate, pageLabel, useKindWord } from "./shared";
import { w } from "./words";

type Link = Schemas["LinkOut"];

const entryWords = (word: (kind: string) => string, kind: string, date: string | null | undefined, page: number) => `${word(kind)} · ${longDate(date)} · ${pageLabel(page)}`;

function LinkRow({ link, question, canDecide, onDecide }: { link: Link; question: string; canDecide: boolean; onDecide: (status: "confirmed" | "rejected") => Promise<void> }) {
  const word = useKindWord();
  const { busy, run } = useBusy();
  const nameId = `link-${link.id}`;
  return (
    <li aria-labelledby={nameId} className="av-understand-event">
      <strong id={nameId}>{question}</strong>
      <span>{entryWords(word, link.kind, link.event_date, link.page_no)}</span>
      {link.reason && <span className="av-muted">{link.reason}</span>}
      <span className="av-muted">{w(`links.status.${link.status as "suggested" | "confirmed" | "rejected" | "stale"}`)}</span>
      {canDecide && link.status === "suggested" && (
        <span className="av-row">
          <Button busy={busy} onClick={() => run(() => onDecide("confirmed"))}>{w("links.confirm")}</Button>
          <Button variant="secondary" busy={busy} onClick={() => run(() => onDecide("rejected"))}>{w("links.reject")}</Button>
        </span>
      )}
    </li>
  );
}

function AddLink({ caseId, questions, onAdded }: { caseId: string; questions: Schemas["Questions"]["questions"]; onAdded: (l: Link) => void }) {
  const checked = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/timeline", { params: { path: { case_id: caseId }, query: { status: "verified" } } })), [caseId], { cache: "understand/LinkReview#AddLink:checked" });
  const word = useKindWord();
  const [factId, setFactId] = useState("");
  const [questionId, setQuestionId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const entries = (checked.data?.lanes ?? []).flatMap((l) => l.events);
  const add = async () => {
    setError(null);
    try {
      const out = await unwrap(api.POST("/v1/cases/{case_id}/fact-links", { ...casePath(caseId), body: { fact_id: factId, question_id: questionId } }));
      setFactId("");
      setQuestionId("");
      onAdded(out);
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <div className="av-stack">
      <h3>{w("links.add_title")}</h3>
      <div className="av-field">
        <label className="av-field__label" htmlFor="link-entry">{w("links.entry")}</label>
        <select id="link-entry" className="av-input" value={factId} onChange={(e) => setFactId(e.target.value)}>
          <option value="">{w("links.choose")}</option>
          {entries.map((e) => (
            <option key={e.fact_id} value={e.fact_id}>{entryWords(word, e.kind, e.event_date, e.page_no)}</option>
          ))}
        </select>
      </div>
      <div className="av-field">
        <label className="av-field__label" htmlFor="link-question">{w("links.question")}</label>
        <select id="link-question" className="av-input" value={questionId} onChange={(e) => setQuestionId(e.target.value)}>
          <option value="">{w("links.choose")}</option>
          {questions.map((q) => (
            <option key={q.id} value={q.id}>{w("timeline.filter.question.option", { n: q.number })}</option>
          ))}
        </select>
      </div>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      <div>
        <Button disabled={!factId || !questionId} onClick={() => add()}>{w("links.add")}</Button>
      </div>
    </div>
  );
}

export function LinkReview({ caseId, can }: { caseId: string; can: (action: string) => boolean }) {
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/fact-links", casePath(caseId))), [caseId], { cache: "understand/LinkReview#LinkReview:found" });
  const asked = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/questions", casePath(caseId))), [caseId], { cache: "understand/LinkReview#LinkReview:asked" });
  const [changed, setChanged] = useState<Record<string, Link>>({});
  const [added, setAdded] = useState<Link[]>([]);
  const [started, setStarted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recheck, setRecheck] = useState<Link | null>(null);
  useEffect(() => {
    setChanged({});
    setAdded([]);
  }, [found.data]);
  const questions = asked.data?.questions ?? [];
  const number = new Map(questions.map((q) => [q.id, q.number]));
  const name = (l: Link) => (l.question_id && number.has(l.question_id) ? w("timeline.filter.question.option", { n: number.get(l.question_id)! }) : w("links.no_question"));
  const links = [...(found.data?.links ?? []).map((l) => changed[l.id] ?? l), ...added];
  const holding = links.filter((l) => l.status !== "stale");
  const stale = links.filter((l) => l.status === "stale");
  const decide = async (link: Link, status: "confirmed" | "rejected") => {
    setError(null);
    setRecheck(null);
    try {
      const out = await unwrap(
        api.PATCH("/v1/cases/{case_id}/fact-links/{link_id}", {
          params: { path: { case_id: caseId, link_id: link.id } },
          body: { status },
          headers: ifMatch(`"${link.version}"`),
        }),
      );
      setChanged((c) => ({ ...c, [out.id]: out }));
    } catch (err) {
      setError(errorMessage(err));
      if (err instanceof ApiError && err.code === "not_checked") setRecheck(link);
    }
  };
  const suggest = async () => {
    setError(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/fact-links/suggest", casePath(caseId)));
      setStarted(true);
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <section aria-labelledby="things-links" className="av-card av-stack">
      <div>
        <h2 id="things-links">{w("links.title")}</h2>
        <p className="av-muted">{w("links.hint")}</p>
      </div>
      {holding.length === 0 ? (
        <p className="av-muted">{w("links.none")}</p>
      ) : (
        <ul className="av-understand-list">
          {holding.map((l) => (
            <LinkRow key={l.id} link={l} question={name(l)} canDecide={can("link.decide")} onDecide={(s) => decide(l, s)} />
          ))}
        </ul>
      )}
      {can("link.suggest") && (
        <div>
          <Button variant="secondary" onClick={() => suggest()}>{w("links.suggest")}</Button>
        </div>
      )}
      {started && <Notice><p>{w("links.suggest_started")}</p></Notice>}
      {error && (
        <Notice tone="error">
          <p>{error}</p>
          {recheck && <LinkButton to={`${screenPath("reader", { caseId, params: { page: recheck.page_no } }) ?? `/cases/${caseId}/read/${recheck.page_no}`}?fact=${recheck.fact_id}`}>{w("links.recheck")}</LinkButton>}
        </Notice>
      )}
      {stale.length > 0 && (
        <div className="av-stack">
          <h3>{w("links.stale_title")}</h3>
          <p className="av-muted">{w("links.stale_hint")}</p>
          <ul className="av-understand-list">
            {stale.map((l) => (
              <LinkRow key={l.id} link={l} question={name(l)} canDecide={false} onDecide={async () => undefined} />
            ))}
          </ul>
        </div>
      )}
      {can("link.decide") && <AddLink caseId={caseId} questions={questions} onAdded={(l) => setAdded((a) => [...a, l])} />}
    </section>
  );
}
