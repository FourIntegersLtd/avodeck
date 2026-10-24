// S14 Points against (docs/design/screens/S14-PointsAgainst.dc.html): the facts for one question on each side
// (the sides' words come from the pack through the API), facts not yet used, relevant facts the draft has not
// cited, accounts that differ from the record, and the expert's own note on the range of reasonable practice.
// The server decides every change; this screen sends the version it saw.
import { useState } from "react";
import { useParams } from "react-router-dom";
import { formatDue } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, DictationBar, LinkButton, LoadError, Loading, Menu, MenuItem, MenuSection, Notice, PageRef, TextLink, Updating } from "../../foundation/ui";
import { ActionLink, KindBadge, QuestionTabs, ReasonDialog, versionTag, WithQuestion, type Chosen } from "./shared";
import { w } from "./words";

type Data = Schemas["Points"];
type Point = Schemas["PointOut"];
const VERDICTS = ["supports", "contradicts", "not_addressed"] as const;
const verdictLabel = (verdict: string) =>
  (VERDICTS as readonly string[]).includes(verdict) ? w(`points.verdict.${verdict as (typeof VERDICTS)[number]}`) : verdict;

export function Points() {
  const { caseId = "" } = useParams();
  return <WithQuestion caseId={caseId}>{(chosen) => <PointsLoader key={chosen.current.id} caseId={caseId} chosen={chosen} />}</WithQuestion>;
}

function PointsLoader({ caseId, chosen }: { caseId: string; chosen: Chosen }) {
  const questionId = chosen.current.id;
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/questions/{question_id}/points", { params: { path: { case_id: caseId, question_id: questionId } } })), [caseId, questionId]);
  if (found.error) return <div className="av-page"><LoadError onRetry={found.reload} /></div>;
  if (!found.data) return <div className="av-page"><Loading /></div>;
  return <PointsView caseId={caseId} chosen={chosen} initial={found.data} />;
}

function PointsView({ caseId, chosen, initial }: { caseId: string; chosen: Chosen; initial: Data }) {
  const questionId = chosen.current.id;
  const [data, setData] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<Point | null>(null);
  const [note, setNote] = useState(initial.range_note.text ?? "");
  const [noteVersion, setNoteVersion] = useState(initial.range_note.version);
  const [noteSaved, setNoteSaved] = useState(false);
  const [adding, setAdding] = useState<string | null>(null);
  const [dictatedOn, setDictatedOn] = useState(initial.range_note.dictated_on);

  const [existing, setExisting] = useState<string | null>(null);
  const [moving, setMoving] = useState<string | null>(null);

  const attempt = async (work: () => Promise<void>): Promise<void> => {
    setError(null);
    setExisting(null);
    try {
      await work();
    } catch (err) {
      setError(errorMessage(err, { stale: "write.points.stale", conflict: "write.points.exists" }));
    }
  };

  const move = async (point: Point, side: string) => {
    if (moving) return;
    setMoving(point.id);
    try {
      await change(point, { side });
    } finally {
      setMoving(null);
    }
  };

  const change = (point: Point, body: Schemas["PointChange"]) =>
    attempt(async () => {
      const next = await unwrap(api.PATCH("/v1/cases/{case_id}/points/{point_id}", { params: { path: { case_id: caseId, point_id: point.id } }, body, headers: ifMatch(versionTag(point.version)) }));
      setData((d) => ({ ...d, points: next.status === "dismissed" ? d.points.filter((p) => p.id !== point.id) : d.points.map((p) => (p.id === point.id ? next : p)) }));
      setRemoving(null);
    });

  const add = (fact: Schemas["PointFact"], side: string) =>
    attempt(async () => {
      let point: Point;
      try {
        point = await unwrap(api.POST("/v1/cases/{case_id}/questions/{question_id}/points", { params: { path: { case_id: caseId, question_id: questionId } }, body: { fact_id: fact.id, side } }));
      } catch (err) {
        if (err instanceof ApiError && err.code === "conflict") {
          // Already a point (another window): load the latest so the existing point can be shown.
          const latest = await unwrap(api.GET("/v1/cases/{case_id}/questions/{question_id}/points", { params: { path: { case_id: caseId, question_id: questionId } } }));
          setData(latest);
          setExisting(latest.points.find((p) => p.fact.id === fact.id)?.id ?? null);
        }
        throw err;
      }
      setData((d) => ({ ...d, points: [...d.points, point], unused: d.unused.filter((f) => f.id !== fact.id), relevant: d.relevant.filter((r) => r.fact.id !== fact.id) }));
    });

  const dismissRelevant = (relevant: Schemas["RelevantOut"]) =>
    attempt(async () => {
      await unwrap(api.PATCH("/v1/cases/{case_id}/relevant-facts/{relevant_id}", { params: { path: { case_id: caseId, relevant_id: relevant.id } }, body: { status: "dismissed" }, headers: ifMatch(versionTag(relevant.version)) }));
      setData((d) => ({ ...d, relevant: d.relevant.filter((r) => r.id !== relevant.id) }));
    });

  const decide = (claim: Schemas["ClaimOut"], status: "confirmed" | "dismissed") =>
    attempt(async () => {
      await unwrap(api.PATCH("/v1/cases/{case_id}/witness-claims/{claim_id}", { params: { path: { case_id: caseId, claim_id: claim.id } }, body: { status }, headers: ifMatch(versionTag(claim.version)) }));
      setData((d) => ({ ...d, claims: d.claims.filter((c) => c.id !== claim.id) }));
    });

  const saveNote = () =>
    attempt(async () => {
      const saved = await unwrap(api.PUT("/v1/cases/{case_id}/questions/{question_id}/range-note", { params: { path: { case_id: caseId, question_id: questionId } }, body: { text: note }, headers: ifMatch(versionTag(noteVersion)) }));
      setNoteVersion(saved.version);
      setDictatedOn(saved.dictated_on);
      setNoteSaved(true);
    });

  const done = chosen.progress?.questions.find((p) => p.question_id === questionId)?.points_done ?? false;
  const live = data.points.filter((p) => p.status !== "dismissed");
  return (
    <div className="av-page av-stack">
      <div>
        <h2 className="av-write-title">{w("points.title")}</h2>
        <p className="av-muted">{w("points.intro")}</p>
      </div>
      <QuestionTabs caseId={caseId} chosen={chosen} screen="points" />
      <p className="av-muted av-visually-hidden">
        <KindBadge header={data.question} /> {chosen.current.confirmed_text ?? chosen.current.text_verbatim}
      </p>
      {error && (
        <Notice tone="error">
          <p>{error}</p>
          {existing && (
            <button
              type="button"
              className="av-text-link av-write-linkbutton"
              onClick={() => {
                const el = document.getElementById(`point-${existing}`);
                el?.scrollIntoView?.({ block: "center" });
                el?.focus();
              }}
            >
              {w("points.exists_show")}
            </button>
          )}
        </Notice>
      )}

      <div className="av-write-columns">
        {data.sides.map((side) => {
          const mine = live.filter((p) => p.side === side.code);
          return (
            <section key={side.code} className="av-card" aria-labelledby={`side-${side.code}`}>
              <h3 id={`side-${side.code}`}>{side.label}</h3>
              {mine.length === 0 && <p className="av-muted">{w("points.side_empty")}</p>}
              <ul className="av-write-list">
                {mine.map((point) => (
                  <li key={point.id} id={`point-${point.id}`} tabIndex={-1} className={moving === point.id ? "av-write-item av-busy-dim" : "av-write-item"} aria-busy={moving === point.id}>
                    {moving === point.id && <Updating label={w("points.moving")} />}
                    <div className="av-write-item__head">
                      <span>{point.fact.value}</span>
                      <PageRef page={point.fact.page_no} />
                    </div>
                    {point.flagged && <p className="av-muted">{w("points.flagged")}</p>}
                    {point.status === "stale" && <p className="av-write-badge">{w("points.stale_point")}</p>}
                    <div className="av-write-actions">
                      {(point.status === "suggested" || point.status === "stale") && (
                        <Button variant="quiet" onClick={() => change(point, { status: "accepted" })}>
                          {w("points.keep")}
                        </Button>
                      )}
                      <Menu label={w("points.more")} trigger={w("points.more")}>
                        <MenuSection>
                          {data.sides
                            .filter((s) => s.code !== point.side)
                            .map((s) => (
                              <MenuItem key={s.code} onClick={() => void move(point, s.code)}>
                                {w("points.move", { side: s.label })}
                              </MenuItem>
                            ))}
                          <MenuItem onClick={() => setRemoving(point)}>{w("points.remove")}</MenuItem>
                        </MenuSection>
                      </Menu>
                    </div>
                  </li>
                ))}
              </ul>
              {data.unused.length > 0 && (
                <div className="av-write-item av-write-item__head av-write-addrow">
                  <span className="av-muted">{w("points.add_row")}</span>
                  <button type="button" className="av-text-link av-write-linkbutton" aria-expanded={adding === side.code} onClick={() => setAdding(adding === side.code ? null : side.code)}>
                    {w("points.add_row_button")}
                  </button>
                </div>
              )}
              {adding === side.code && data.unused.length > 0 && (
                <section aria-labelledby="unused-title">
                  <h4 id="unused-title" className="av-visually-hidden">{w("points.unused")}</h4>
                  <ul className="av-write-list">
                    {data.unused.map((fact) => (
                      <li key={fact.id} className="av-write-item av-write-item__head">
                        <span>{fact.value} <PageRef page={fact.page_no} /></span>
                        <ActionLink onClick={() => add(fact, side.code)}>
                          {w("points.add_to", { side: side.label })}
                        </ActionLink>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </section>
          );
        })}
      </div>

      {data.relevant.map((r) => (
        <Notice key={r.id} tone="amber">
          <p>
            <strong>{w("points.relevant_title", { count: 1 })}</strong>
          </p>
          <p>
            <span>{r.fact.value}</span> <PageRef page={r.fact.page_no} /> <span>{r.why_relevant}</span>
          </p>
          <div className="av-write-actions">
            <TextLink to={`/cases/${caseId}/consultation/${r.fact.id}`}>{w("points.relevant_look")}</TextLink>
            <Button variant="quiet" onClick={() => dismissRelevant(r)}>
              {w("points.relevant_dismiss")}
            </Button>
          </div>
        </Notice>
      ))}

      <div className="av-write-columns">
        <section className="av-card av-stack av-write-note">
          <h3>{data.range_note.prompt}</h3>
          <DictationBar
            caseId={caseId}
            context="range_note"
            label={data.range_note.prompt}
            value={note}
            onChange={(value) => {
              setNote(value);
              setNoteSaved(false);
            }}
            rows={6}
          />
          <div className="av-write-actions">
            <Button variant="secondary" onClick={saveNote}>{w("points.range_save")}</Button>
            {noteSaved && <span role="status">{w("points.range_saved")}</span>}
            {dictatedOn && <span className="av-muted">{w("points.dictated", { date: formatDue(dictatedOn, undefined) })}</span>}
          </div>
        </section>

        <section className="av-card av-stack" aria-labelledby="differ-title">
          <h3 id="differ-title">{w("points.differ")}</h3>
          {data.claims.length === 0 && <p className="av-muted">{w("points.differ_none")}</p>}
          {data.claims.map((claim) => (
            <article key={claim.id} className="av-stack" aria-label={claim.text_verbatim}>
              <p>
                <span className="av-write-badge">{verdictLabel(claim.expert_verdict ?? claim.verdict)}</span>
              </p>
              {claim.status === "stale" && <p className="av-muted">{w("points.claim_stale")}</p>}
              <p>
                <strong>{w("points.statement")}</strong> {claim.text_verbatim}
              </p>
              <p>
                <strong>{w("points.record")}</strong> {claim.facts.length === 0 && w("points.record_none")}
                {claim.facts.map((f) => (
                  <span key={f.id}>
                    {f.value} <PageRef page={f.page_no} />{" "}
                  </span>
                ))}
              </p>
              <div className="av-write-actions">
                <ActionLink onClick={() => decide(claim, "confirmed")}>
                  {w("points.claim_confirm")}
                </ActionLink>
                <ActionLink onClick={() => decide(claim, "dismissed")}>
                  {w("points.claim_dismiss")}
                </ActionLink>
              </div>
            </article>
          ))}
          {data.claims.length > 0 && <p className="av-write-grey">{w("points.judge")}</p>}
        </section>
      </div>

      <div className="av-write-footer av-write-footer--between">
        {done && (
          <p role="status" className="av-write-done">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="12" cy="12" r="9" />
              <path d="M8 12l3 3 5-6" />
            </svg>
            {w("points.done", { number: chosen.current.number })}
          </p>
        )}
        <LinkButton arrow to={`/cases/${caseId}/opinion?q=${questionId}`}>{w("points.write")}</LinkButton>
      </div>

      {removing && (
        <ReasonDialog
          title={w("points.remove_title")}
          label={w("points.remove_reason")}
          confirm={w("points.remove_confirm")}
          onConfirm={(reason) => change(removing, { status: "dismissed", dismissed_reason: reason })}
          onClose={() => setRemoving(null)}
        />
      )}
    </div>
  );
}

