// S14 Points against (docs/design/screens/S14-PointsAgainst.dc.html): the facts for one question on each side
// (the sides' words come from the pack through the API), facts not yet used, relevant facts the draft has not
// cited, accounts that differ from the record, and the expert's own note on the range of reasonable practice.
// The server decides every change; this screen sends the version it saw.
import { useState } from "react";
import { useParams } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, DictationBox, LinkButton, LoadError, Loading, Notice } from "../../foundation/ui";
import { pageLabel, QuestionTabs, ReasonDialog, versionTag, WithQuestion, type Chosen } from "./shared";
import { w } from "./words";

type Data = Schemas["Points"];
type Point = Schemas["PointOut"];

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

  const attempt = async (work: () => Promise<void>) => {
    setError(null);
    try {
      await work();
    } catch (err) {
      setError(errorMessage(err, { stale: "write.points.stale" }));
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
      const point = await unwrap(api.POST("/v1/cases/{case_id}/questions/{question_id}/points", { params: { path: { case_id: caseId, question_id: questionId } }, body: { fact_id: fact.id, side } }));
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
      setNoteSaved(true);
    });

  const live = data.points.filter((p) => p.status !== "dismissed");
  return (
    <div className="av-page av-stack">
      <div>
        <h2>{w("points.title")}</h2>
        <p className="av-muted">{w("points.intro")}</p>
      </div>
      <QuestionTabs caseId={caseId} chosen={chosen} screen="points" />
      {error && (
        <Notice tone="error">
          <p>{error}</p>
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
                  <li key={point.id} className="av-write-item">
                    <div className="av-write-item__head">
                      <span>{point.fact.value}</span>
                      <span className="av-write-page">{pageLabel(point.fact.page_no)}</span>
                    </div>
                    {point.flagged && <p className="av-muted">{w("points.flagged")}</p>}
                    <div className="av-write-actions">
                      {point.status === "suggested" && (
                        <Button variant="secondary" onClick={() => void change(point, { status: "accepted" })}>
                          {w("points.keep")}
                        </Button>
                      )}
                      {data.sides
                        .filter((s) => s.code !== point.side)
                        .map((s) => (
                          <Button key={s.code} variant="secondary" onClick={() => void change(point, { side: s.code })}>
                            {w("points.move", { side: s.label })}
                          </Button>
                        ))}
                      <Button variant="secondary" onClick={() => setRemoving(point)}>
                        {w("points.remove")}
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </div>

      <section className="av-card" aria-labelledby="unused-title">
        <h3 id="unused-title">{w("points.unused")}</h3>
        {data.unused.length === 0 && <p className="av-muted">{w("points.unused_empty")}</p>}
        <ul className="av-write-list">
          {data.unused.map((fact) => (
            <li key={fact.id} className="av-write-item">
              <div className="av-write-item__head">
                <span>{fact.value}</span>
                <span className="av-write-page">{pageLabel(fact.page_no)}</span>
              </div>
              <div className="av-write-actions">
                {data.sides.map((s) => (
                  <Button key={s.code} variant="secondary" onClick={() => void add(fact, s.code)}>
                    {w("points.add_to", { side: s.label })}
                  </Button>
                ))}
              </div>
            </li>
          ))}
        </ul>
      </section>

      {data.relevant.length > 0 && (
        <Notice tone="warning">
          <p>
            <strong>{w("points.relevant_title", { count: data.relevant.length })}</strong>
          </p>
          <ul className="av-write-list">
            {data.relevant.map((r) => (
              <li key={r.id} className="av-write-item">
                <div className="av-write-item__head">
                  <span>{r.fact.value}</span>
                  <span className="av-write-page">{pageLabel(r.fact.page_no)}</span>
                </div>
                <p>{r.why_relevant}</p>
                <Button variant="secondary" onClick={() => void dismissRelevant(r)}>
                  {w("points.relevant_dismiss")}
                </Button>
              </li>
            ))}
          </ul>
        </Notice>
      )}

      <div className="av-write-columns">
        <section className="av-card av-stack">
          <DictationBox
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
            <Button onClick={() => void saveNote()}>{w("points.range_save")}</Button>
            {noteSaved && <span role="status">{w("points.range_saved")}</span>}
          </div>
        </section>

        <section className="av-card av-stack" aria-labelledby="differ-title">
          <h3 id="differ-title">{w("points.differ")}</h3>
          {data.claims.length === 0 && <p className="av-muted">{w("points.differ_none")}</p>}
          {data.claims.map((claim) => (
            <article key={claim.id} className="av-stack">
              <div className="av-write-pair">
                <div>
                  <strong>{w("points.statement")}</strong>
                  <p>{claim.text_verbatim}</p>
                </div>
                <div>
                  <strong>{w("points.record")}</strong>
                  {claim.facts.length === 0 && <p>{w("points.record_none")}</p>}
                  {claim.facts.map((f) => (
                    <p key={f.id}>
                      <span>{f.value}</span> <span className="av-write-page">{pageLabel(f.page_no)}</span>
                    </p>
                  ))}
                </div>
              </div>
              <div className="av-write-actions">
                <Button variant="secondary" onClick={() => void decide(claim, "confirmed")}>
                  {w("points.claim_confirm")}
                </Button>
                <Button variant="secondary" onClick={() => void decide(claim, "dismissed")}>
                  {w("points.claim_dismiss")}
                </Button>
              </div>
            </article>
          ))}
          {data.claims.length > 0 && <p className="av-muted">{w("points.judge")}</p>}
        </section>
      </div>

      <div className="av-write-footer">
        <LinkButton to={`/cases/${caseId}/opinion?q=${questionId}`}>{w("points.write")}</LinkButton>
      </div>

      {removing && (
        <ReasonDialog
          title={w("points.remove_title")}
          label={w("points.remove_reason")}
          confirm={w("points.remove_confirm")}
          onConfirm={(reason) => void change(removing, { status: "dismissed", dismissed_reason: reason })}
          onClose={() => setRemoving(null)}
        />
      )}
    </div>
  );
}

