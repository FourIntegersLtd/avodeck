// S20 Questions from the other side (docs/design/screens/S20-Questions35.dc.html): each set (round) with its deadline, the
// questions as they were written with a suggested classification, the expert's own answer (If-Match), a note to the
// instructing party, and signing the answers behind the code (step-up). The name of this screen, the party and the
// declaration wording come from the pack.
import { useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { t } from "../../foundation/i18n";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorMessage } from "../../foundation/lib/errors";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Checkbox, Dialog, DictationBox, LinkButton, LoadError, Loading, Notice, TextField } from "../../foundation/ui";
import { capitalise, declarationParts, keyName, longDate, Select, useLocale, useWaiting, versionTag } from "./shared";
import { w } from "./words";

type Round = Schemas["RoundOut"];
type Question = Schemas["app__features__after_serving__routes_questions__QuestionOut"];
const ERRORS: Record<string, string> = {
  stale: "after_serving.error.stale",
  invalid: "after_serving.error.invalid",
  answers_missing: "after_serving.questions.error.answers_missing",
  already_signed: "after_serving.questions.error.already_signed",
  locked: "after_serving.questions.error.locked",
  declaration_confirmed: "after_serving.questions.error.declaration_confirmed",
};

export function QuestionsScreen() {
  const { caseId = "" } = useParams();
  const [params] = useSearchParams();
  const document = params.get("document");
  const locale = useLocale();
  const { pack } = useCaseFrame();
  const path = { params: { path: { case_id: caseId } } };
  const rounds = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/written-question-rounds", path)), [caseId]);
  const questions = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/written-questions", path)), [caseId]);
  const deadlines = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/deadlines", path)), [caseId]);
  const wait = useWaiting(() => { rounds.reload(); questions.reload(); }, questions.data?.questions.length);

  if (rounds.error || questions.error || deadlines.error) {
    return <div className="av-page"><LoadError onRetry={() => { rounds.reload(); questions.reload(); deadlines.reload(); }} /></div>;
  }
  if (!rounds.data || !questions.data || !deadlines.data) return <div className="av-page"><Loading /></div>;
  const vocab = (pack.vocabulary as Record<string, unknown>).written_questions;
  const classes = [...new Set(questions.data.questions.flatMap((q) => [q.classification, q.suggested_classification]).filter((c): c is string => !!c))];
  const reloadAll = () => { rounds.reload(); questions.reload(); deadlines.reload(); };

  return (
    <div className="av-page av-stack">
      <LinkButton variant="quiet" to={`/cases/${caseId}/served`}>{w("back")}</LinkButton>
      <h2>{capitalise(String(vocab ?? ""))}</h2>
      <p>{w("questions.intro")}</p>
      {wait.waiting && <Notice><p>{w("questions.reading")}</p></Notice>}
      {rounds.data.rounds.length === 0 && <Notice><p>{w("questions.none")}</p></Notice>}
      {rounds.data.rounds.map((round) => (
        <RoundView
          key={round.id}
          caseId={caseId}
          round={round}
          questions={questions.data!.questions.filter((q) => q.round_id === round.id)}
          classes={classes}
          due={deadlines.data!.deadlines.find((d) => d.id === round.deadline_id)?.due_on}
          locale={locale}
          declarations={pack.declarations}
          party={partyWord(pack)}
          onImport={() => wait.start()}
          onChanged={reloadAll}
        />
      ))}
      <AddRound caseId={caseId} document={document} onDone={reloadAll} />
    </div>
  );
}

function partyWord(pack: Schemas["CasePack"]): string {
  return (pack.instructing_party_types as Array<{ key: string; label?: string }>)[0]?.label ?? "";
}

function RoundView({ caseId, round, questions, classes, due, locale, declarations, party, onImport, onChanged }: {
  caseId: string; round: Round; questions: Question[]; classes: string[]; due?: string; locale: string;
  declarations: Schemas["CasePack"]["declarations"]; party: string; onImport: () => void; onChanged: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [bump, setBump] = useState(0);
  const importSet = async () => {
    setError(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/written-questions/import", { params: { path: { case_id: caseId } }, body: { round_id: round.id } }));
      onImport();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  return (
    <section className="av-card av-stack" aria-labelledby={`round-${round.id}`}>
      <h3 id={`round-${round.id}`}>{w("questions.round", { number: round.number, date: longDate(round.received_on, locale) })}</h3>
      {due && <p className="av-after-mono">{w("questions.due", { date: longDate(due, locale) })}</p>}
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {round.status === "open" && !round.extracted_at && <Button variant="secondary" onClick={() => void importSet()}>{w("questions.read")}</Button>}
      <ul className="av-after-list">
        {questions.map((q) => (
          <QuestionCard key={`${q.id}-${bump}`} caseId={caseId} question={q} classes={classes} party={party} onReload={() => { setBump(bump + 1); onChanged(); }} />
        ))}
      </ul>
      {round.status === "answered" ? (
        <Notice tone="success"><p>{w("questions.signed")}</p></Notice>
      ) : (
        questions.length > 0 && <SignAnswers caseId={caseId} roundId={round.id} declarations={declarations} onDone={onChanged} />
      )}
    </section>
  );
}

function QuestionCard({ caseId, question, classes, party, onReload }: { caseId: string; question: Question; classes: string[]; party: string; onReload: () => void }) {
  const [version, setVersion] = useState(question.version);
  const [answer, setAnswer] = useState(question.answer_text ?? "");
  const [classification, setClassification] = useState(question.classification ?? question.suggested_classification ?? "");
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState<Schemas["QuestionChange"] | null>(null);
  const [note, setNote] = useState<Schemas["Note"] | null>(null);
  const [told, setTold] = useState(question.told_instructing_party_at);
  const locale = useLocale();
  const endpoint = { case_id: caseId, question_id: question.id };

  const patch = async (body: Schemas["QuestionChange"], at = version) => {
    setError(null);
    setSaved(false);
    try {
      const out = await unwrap(api.PATCH("/v1/cases/{case_id}/written-questions/{question_id}", { params: { path: endpoint }, body, headers: ifMatch(versionTag(at)) }));
      setVersion(out.version);
      setSaved(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 412) setStale(body);
      else setError(errorMessage(err, ERRORS));
    }
  };
  const keepMine = async () => {
    const body = stale;
    setStale(null);
    try {
      const fresh = await unwrap(api.GET("/v1/cases/{case_id}/written-questions", { params: { path: { case_id: caseId } } }));
      if (body) await patch(body, fresh.questions.find((q) => q.id === question.id)?.version ?? version);
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  const tell = async () => {
    setError(null);
    try {
      setNote(await unwrap(api.POST("/v1/cases/{case_id}/written-questions/{question_id}/tell-instructing-party", { params: { path: endpoint } })));
      setTold(new Date().toISOString());
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  const options = classes.includes(classification) || !classification ? classes : [...classes, classification];

  return (
    <li className="av-after-item av-stack">
      <h4>{w("questions.number", { number: question.number })}</h4>
      <p className="av-after-words">{question.text_verbatim}</p>
      {question.para_ref && <p className="av-muted">{w("questions.about", { para: question.para_ref })}</p>}
      <Select
        label={w("questions.classification")}
        value={classification}
        onChange={(e) => { setClassification(e.target.value); void patch({ classification: e.target.value }); }}
      >
        {!classification && <option value="" />}
        {options.map((c) => <option key={c} value={c}>{keyName("classification", c)}</option>)}
      </Select>
      {!question.classification && question.suggested_classification && <span className="av-badge">{w("questions.suggested")}</span>}
      <DictationBox caseId={caseId} context="written_question_answer" label={w("questions.answer")} value={answer} onChange={(v) => { setAnswer(v); setSaved(false); }} rows={6} />
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {saved && <p role="status">{w("questions.saved")}</p>}
      {note && <Notice><h5>{w("questions.tell_title")}</h5><p>{t(note.key, note.params as Record<string, string | number>)}</p></Notice>}
      {told && !note && <p className="av-muted">{w("questions.told", { date: longDate(told, locale) })}</p>}
      <div className="av-row">
        <Button variant="secondary" onClick={() => void tell()}>{w("questions.tell", { party: party || w("questions.tell_party") })}</Button>
        <Button onClick={() => void patch({ answer_text: answer })}>{w("questions.answer_save")}</Button>
      </div>
      {stale && (
        <Dialog title={w("stale_title")} onClose={() => setStale(null)}>
          <div className="av-stack">
            <Button onClick={() => void keepMine()}>{w("stale_mine")}</Button>
            <Button variant="secondary" onClick={onReload}>{w("stale_theirs")}</Button>
          </div>
        </Dialog>
      )}
    </li>
  );
}

function SignAnswers({ caseId, roundId, declarations, onDone }: { caseId: string; roundId: string; declarations: Schemas["CasePack"]["declarations"]; onDone: () => void }) {
  const stepUp = useStepUp();
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sign = async () => {
    setError(null);
    setBusy(true);
    try {
      const out = await stepUp(() => unwrap(api.POST("/v1/cases/{case_id}/written-questions/answers/sign", { params: { path: { case_id: caseId } }, body: { declaration_confirmed: true, round_id: roundId } })));
      if (out) onDone();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="av-stack">
      <h4>{w("questions.declaration")}</h4>
      {declarationParts(declarations).map((part) => <p key={part} className="av-after-words">{part}</p>)}
      <Checkbox label={w("questions.confirm")} checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      <div className="av-after-footer">
        <Button disabled={!confirmed || busy} onClick={() => void sign()}>{w("questions.sign")}</Button>
      </div>
    </div>
  );
}

function AddRound({ caseId, document, onDone }: { caseId: string; document: string | null; onDone: () => void }) {
  const [receivedOn, setReceivedOn] = useState("");
  const [dueOn, setDueOn] = useState("");
  const [error, setError] = useState<string | null>(null);
  const add = async () => {
    if (!document) return;
    setError(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/written-question-rounds", { params: { path: { case_id: caseId } }, body: { document_id: document, received_on: receivedOn, answers_due_on: dueOn } }));
      onDone();
    } catch (err) {
      setError(errorMessage(err, ERRORS));
    }
  };
  return (
    <section className="av-card av-stack" aria-labelledby="add-round">
      <h3 id="add-round">{w("questions.add")}</h3>
      {!document && <p>{w("questions.document_needed")}</p>}
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      <TextField label={w("questions.received_on")} name="received_on" type="date" value={receivedOn} onChange={(e) => setReceivedOn(e.target.value)} />
      <TextField label={w("questions.answers_due_on")} name="answers_due_on" type="date" value={dueOn} onChange={(e) => setDueOn(e.target.value)} />
      <div className="av-after-footer">
        <Button variant="secondary" disabled={!document || !receivedOn || !dueOn} onClick={() => void add()}>{w("questions.add_submit")}</Button>
      </div>
    </section>
  );
}
