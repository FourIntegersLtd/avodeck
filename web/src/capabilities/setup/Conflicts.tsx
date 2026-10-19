// S05 A quick check before you start: the pack's conflict questions (a yes needs a few words), the
// paragraph the answers add to the report, whether the profile's roles cover the case period, the balance
// of sides, declaring something new mid-case, and stepping back (which needs the code: rule 10).
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Dialog, LoadError, Loading, Notice, Spinner, TextLink } from "../../foundation/ui";
import { Balance, Choice, NoteView, partyWords, TextArea } from "./shared";

type Answer = { answer: "yes" | "no" | null; detail: string };

export function Conflicts() {
  const { caseId = "" } = useParams();
  const path = { params: { path: { case_id: caseId } } };
  const found = useResource(async () => {
    const [state, pack] = await Promise.all([unwrap(api.GET("/v1/cases/{case_id}/conflicts", path)), unwrap(api.GET("/v1/cases/{case_id}/pack", path))]);
    return { state, pack };
  }, [caseId]);
  if (found.error) return <div className="av-page"><LoadError onRetry={found.reload} /></div>;
  if (!found.data) return <div className="av-page"><Loading /></div>;
  return <ConflictsView caseId={caseId} initial={found.data.state} pack={found.data.pack} />;
}

function ConflictsView({ caseId, initial, pack }: { caseId: string; initial: Schemas["Conflicts"]; pack: Schemas["CasePack"] }) {
  const navigate = useNavigate();
  const stepUp = useStepUp();
  const path = { params: { path: { case_id: caseId } } };
  const [state, setState] = useState(initial);
  const [answers, setAnswers] = useState<Record<string, Answer>>(
    Object.fromEntries(initial.questions.map((q) => [q.key, { answer: q.answer, detail: q.detail ?? "" }])),
  );
  const [tried, setTried] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<Schemas["NoteDraft"] | null>(null);
  const [leaving, setLeaving] = useState(false);
  const [reason, setReason] = useState("");
  const [withdrawn, setWithdrawn] = useState(false);
  const preparing = useResource(async () => (await unwrap(api.GET("/v1/cases/{case_id}/process", path))).job, [caseId]);
  const job = preparing.data && ["queued", "running"].includes(preparing.data.status) ? preparing.data : null;

  const needsDetail = (q: Schemas["ConflictQuestion"]) => {
    const a = answers[q.key];
    return q.detail_required && a?.answer === "yes" && !a.detail.trim();
  };
  const unsaved = state.questions.some((q) => {
    const a = answers[q.key];
    return a?.answer !== q.answer || (a?.answer === "yes" && a.detail.trim() !== (q.detail ?? ""));
  });
  /** Checks and saves the answers if they changed; false means something needs the expert's attention. */
  const save = async (): Promise<Schemas["Conflicts"] | null> => {
    setTried(true);
    setError(null);
    if (!unsaved && state.complete) return state;
    if (state.questions.some((q) => !answers[q.key]?.answer)) {
      setError(t("setup.conflicts.answer_all"));
      return null;
    }
    if (state.questions.some(needsDetail)) return null;
    const body = {
      answers: state.questions.map((q) => {
        const a = answers[q.key]!;
        const detail = a.answer === "yes" ? a.detail.trim() : "";
        return detail ? { key: q.key, answer: a.answer!, detail } : { key: q.key, answer: a.answer! };
      }),
    };
    try {
      const saved = await unwrap(api.PUT("/v1/cases/{case_id}/conflicts", { ...path, body }));
      setState(saved);
      return saved;
    } catch (err) {
      setError(errorMessage(err, { invalid: "setup.conflicts.invalid" }));
      return null;
    }
  };
  /** One action: save what changed, declare it, then move on (or show the note when declaring a change). */
  const declare = async (moveOn: boolean) => {
    const saved = await save();
    if (!saved) return;
    if (moveOn && saved.declared_at && !saved.changed_since_declared) {
      navigate(`/cases/${caseId}/questions`);
      return;
    }
    try {
      const done = await unwrap(api.POST("/v1/cases/{case_id}/conflicts/declare", path));
      if (moveOn) {
        navigate(`/cases/${caseId}/questions`);
        return;
      }
      setState({ ...saved, declared_at: done.declared_at, outcome: done.outcome, changed_since_declared: false });
      setNote(done.note);
    } catch (err) {
      setError(errorMessage(err, { invalid: "setup.conflicts.answer_all" }));
    }
  };
  const stepBack = async () => {
    setLeaving(false);
    setError(null);
    try {
      const done = await stepUp(() => unwrap(api.POST("/v1/cases/{case_id}/withdraw", { ...path, body: { reason: reason.trim() } })));
      if (!done) return;
      setWithdrawn(true);
      setNote(done.note);
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <div className="av-page av-page--narrow av-stack">
      <div className="av-stack av-setup-heading">
        {job && <span className="av-setup-eyebrow">{t("setup.conflicts.eyebrow")}</span>}
        <h2>{t("setup.conflicts.title")}</h2>
        <p className="av-muted">{t("setup.conflicts.intro")}</p>
      </div>
      {state.warnings.map((key) => (
        <Notice key={key} tone="warning">
          <p>{t(key)}</p>
        </Notice>
      ))}
      {withdrawn && (
        <Notice tone="success">
          <p>{t("setup.withdraw.done")}</p>
        </Notice>
      )}
      <section aria-labelledby="setup-conflict-questions" className="av-card av-stack av-setup-rows">
        <h3 id="setup-conflict-questions">{t("setup.conflicts.questions")}</h3>
        {state.questions.map((q) => {
          const a = answers[q.key] ?? { answer: null, detail: "" };
          return (
            <Choice
              key={q.key}
              legend={q.text}
              name={`conflict-${q.key}`}
              value={a.answer}
              onChange={(v) => setAnswers({ ...answers, [q.key]: { ...a, answer: v as "yes" | "no" } })}
              options={[
                { value: "yes", label: t("setup.yes") },
                { value: "no", label: t("setup.no") },
              ]}
            >
              {a.answer === "yes" && (
                <TextArea
                  label={t("setup.conflicts.detail")}
                  rows={3}
                  value={a.detail}
                  error={tried && needsDetail(q) ? t("setup.conflicts.detail_needed") : undefined}
                  onChange={(e) => setAnswers({ ...answers, [q.key]: { ...a, detail: e.target.value } })}
                />
              )}
            </Choice>
          );
        })}
        {state.coverage_question && <CoverageRow question={state.coverage_question} status={state.coverage.status} />}
        {error && (
          <Notice tone="error">
            <p>{error}</p>
          </Notice>
        )}
      </section>
      {state.paragraph && (
        <section aria-labelledby="setup-conflict-paragraph" className="av-card av-stack">
          <h3 id="setup-conflict-paragraph">{t("setup.conflicts.paragraph")}</h3>
          <p className="av-setup-quote">{state.paragraph}</p>
        </section>
      )}
      <Balance words={partyWords(pack)} />
      {note && <NoteView note={note} />}
      <div className="av-row av-row--spread av-setup-actions">
        {!withdrawn && (
          <Button variant="danger" onClick={() => setLeaving(true)}>
            {t("setup.withdraw.open")}
          </Button>
        )}
        {job && (
          <TextLink className="av-setup-preparing" to={`/cases/${caseId}/preparing`}>
            <Spinner />
            {job.eta_seconds != null ? t("setup.conflicts.preparing", { count: Math.max(1, Math.ceil(job.eta_seconds / 60)) }) : t("setup.conflicts.preparing_unknown")}
          </TextLink>
        )}
        {!withdrawn && state.declared_at && (
          <Button variant="secondary" onClick={() => void declare(false)}>
            {t("setup.conflicts.declare_change")}
          </Button>
        )}
        {!withdrawn && !state.declared_at && <Button onClick={() => void declare(true)}>{t("setup.conflicts.confirm")}</Button>}
      </div>
      {leaving && (
        <Dialog title={t("setup.withdraw.open")} onClose={() => setLeaving(false)}>
          <form
            className="av-stack"
            onSubmit={(e) => {
              e.preventDefault();
              void stepBack();
            }}
          >
            <p>{t("setup.withdraw.intro")}</p>
            <TextArea label={t("setup.withdraw.reason")} rows={3} required value={reason} onChange={(e) => setReason(e.target.value)} />
            <div className="av-row">
              <Button type="submit" variant="danger" disabled={!reason.trim()}>
                {t("setup.withdraw.confirm")}
              </Button>
              <Button variant="secondary" onClick={() => setLeaving(false)}>
                {t("setup.cancel")}
              </Button>
            </div>
          </form>
        </Dialog>
      )}
    </div>
  );
}

/** The specialty's role-coverage question, answered from the profile rather than asked again. */
function CoverageRow({ question, status }: { question: string; status: Schemas["Coverage"]["status"] }) {
  const answer = status === "covered" ? "yes" : status === "partial" ? "partial" : status === "none" ? "no" : "unknown";
  return (
    <div role="group" aria-label={question} className="av-setup-row">
      <span className="av-setup-row__question">
        {question}
        <span className="av-muted av-setup-row__source">{t("setup.conflicts.from_profile")}</span>
      </span>
      <span className="av-setup-answer">{t(`setup.conflicts.coverage.${answer}`)}</span>
    </div>
  );
}
