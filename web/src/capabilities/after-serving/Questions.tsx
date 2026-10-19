// S20 Questions from the other side (docs/design/screens/S20-Questions35.dc.html): each set (round) with its deadline, the
// questions as they were written with a suggested classification, the expert's own answer (If-Match), a note to the
// instructing party, and signing the answers behind the code (step-up). The name of this screen, the party and the
// declaration wording come from the pack.
import { useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { hasMessage, t } from "../../foundation/i18n";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorMessage } from "../../foundation/lib/errors";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { BackLink, Button, Checkbox, Dialog, DictationBox, LoadError, Loading, Notice, PageRef, TextField } from "../../foundation/ui";
import { WarnIcon } from "./icons";
import { capitalise, declarationParts, keyName, longDate, Select, useLocale, useWaiting, versionTag, weekdayDate } from "./shared";
import { w } from "./words";

type Round = Schemas["RoundOut"];
type Question = Schemas["WrittenQuestionOut"];
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
  const { item, pack } = useCaseFrame();
  const path = { params: { path: { case_id: caseId } } };
  const rounds = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/written-question-rounds", path)), [caseId]);
  const questions = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/written-questions", path)), [caseId]);
  const deadlines = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/deadlines", path)), [caseId]);
  const [selected, setSelected] = useState<string | null>(null);
  const wait = useWaiting(() => { rounds.reload(); questions.reload(); }, questions.data?.questions.length);

  if (rounds.error || questions.error || deadlines.error) {
    return <div className="av-page"><LoadError onRetry={() => { rounds.reload(); questions.reload(); deadlines.reload(); }} /></div>;
  }
  if (!rounds.data || !questions.data || !deadlines.data) return <div className="av-page"><Loading /></div>;
  const vocab = (pack.vocabulary as Record<string, unknown>).written_questions;
  const classes = pack.written_question_classifications ?? [];
  const firstOpen = rounds.data.rounds.find((r) => r.status !== "answered");
  const firstDeadline = firstOpen && deadlines.data.deadlines.find((d) => d.id === firstOpen.deadline_id);
  const chip = firstOpen && firstDeadline && firstDeadline.days_left != null
    ? { left: firstDeadline.days_left, due: firstDeadline.due_on, number: firstOpen.number }
    : null;
  const reloadAll = () => { rounds.reload(); questions.reload(); deadlines.reload(); };

  const all = questions.data.questions;
  const current = all.find((q) => q.id === selected) ?? all[0];
  const effective = (q: Question) => q.classification ?? q.suggested_classification ?? "";

  return (
    <div className="av-page av-after-page av-stack">
      <div className="av-after-head">
        <div>
          <BackLink to={`/cases/${caseId}/served`}>{w("back")}</BackLink>
          <h2>{capitalise(String(vocab ?? ""))}</h2>
          <p className="av-muted av-after-lede">{w("questions.intro")}</p>
        </div>
        {chip && (
          <div className="av-after-daycard">
            <strong>{Math.abs(chip.left)}</strong>
            <span>
              <strong>{w(chip.left >= 0 ? "questions.days_left" : "questions.days_over", { count: Math.abs(chip.left) })}</strong>
              <br />
              <small className="av-muted">{w("questions.set_due", { ordinal: ordinal(chip.number), date: weekdayDate(chip.due, locale) })}</small>
            </span>
          </div>
        )}
      </div>
      {wait.waiting && <Notice><p>{w("questions.reading")}</p></Notice>}
      {rounds.data.rounds.length === 0 && <Notice><p>{w("questions.none")}</p></Notice>}
      <div className="av-after-qsplit">
        <div className="av-after-qside">
          {all.length > 0 && (
            <nav aria-label={w("questions.list")}>
              <ul className="av-after-qlist">
                {all.map((q) => (
                  <li key={q.id}>
                    <a
                      href={`#question-${q.number}`}
                      className={`av-after-qnav${q.id === current?.id ? " av-after-qnav--on" : ""}${effective(q) === "goes_beyond" ? " av-after-qnav--flag" : ""}`}
                      aria-current={q.id === current?.id ? "true" : undefined}
                      onClick={() => setSelected(q.id)}
                    >
                      <span className="av-after-qnav__top">
                        <strong>{w("questions.number", { number: q.number })}</strong>
                        <span className={effective(q) === "goes_beyond" ? "av-after-qnav__state av-after-qnav__state--flag" : "av-after-qnav__state"}>
                          {effective(q) === "goes_beyond" ? keyName("classification", "goes_beyond") : keyName("status", q.status)}
                        </span>
                      </span>
                      <span>{q.text_verbatim.length > 80 ? `${q.text_verbatim.slice(0, 80)}…` : q.text_verbatim}</span>
                    </a>
                  </li>
                ))}
              </ul>
            </nav>
          )}
          <AddRound caseId={caseId} document={document} onDone={reloadAll} />
        </div>
        <div className="av-after-qmain av-stack">
          {rounds.data.rounds.map((round) => {
            const mine = all.filter((q) => q.round_id === round.id);
            return (
              <RoundView
                key={round.id}
                caseId={caseId}
                round={round}
                questions={mine}
                shown={mine.length === 0 || mine.some((q) => q.id === current?.id)}
                currentId={current?.id}
                classes={classes}
                locale={locale}
                declarations={pack.declarations}
                party={partyWord(pack, item.instructing_party_type)}
                onImport={() => wait.start()}
                onChanged={reloadAll}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}

function ordinal(number: number): string {
  const key = `after_serving.ordinal.${number}`;
  return hasMessage(key) ? t(key) : w("questions.set_n", { number });
}

function partyWord(pack: Schemas["CasePack"], type: string | null | undefined): string {
  return (pack.instructing_party_types as Array<{ key: string; label?: string }>).find((p) => p.key === type)?.label ?? "";
}

function RoundView({ caseId, round, questions, shown, currentId, classes, locale, declarations, party, onImport, onChanged }: {
  caseId: string; round: Round; questions: Question[]; shown: boolean; currentId?: string; classes: string[]; locale: string;
  declarations: Schemas["CasePack"]["declarations"]; party: string; onImport: () => void; onChanged: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [bump, setBump] = useState(0);
  const [signing, setSigning] = useState(false);
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
    <section className="av-stack" hidden={!shown} aria-label={w("questions.round", { number: round.number, date: longDate(round.received_on, locale) })}>
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {round.status === "open" && !round.extracted_at && <div><Button variant="secondary" onClick={() => void importSet()}>{w("questions.read")}</Button></div>}
      <ul className="av-after-qcards">
        {questions.map((q) => (
          <QuestionCard key={`${q.id}-${bump}`} caseId={caseId} question={q} shown={q.id === currentId} classes={classes} party={party} onReload={() => { setBump(bump + 1); onChanged(); }} />
        ))}
      </ul>
      {round.status === "answered" ? (
        <Notice tone="success"><p>{w("questions.signed")}</p></Notice>
      ) : (
        questions.length > 0 && (
          <div className="av-stack">
            <div><button type="button" className="av-after-linkbtn" aria-expanded={signing} onClick={() => setSigning(!signing)}>{w("questions.sign_open")}</button></div>
            {signing && <section className="av-card"><SignAnswers caseId={caseId} roundId={round.id} declarations={declarations} onDone={onChanged} /></section>}
          </div>
        )
      )}
    </section>
  );
}

function QuestionCard({ caseId, question, shown, classes, party, onReload }: { caseId: string; question: Question; shown: boolean; classes: string[]; party: string; onReload: () => void }) {
  const [changing, setChanging] = useState(false);
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
  const noteKey = classification && hasMessage(`after_serving.classification_note.${classification}`) ? `after_serving.classification_note.${classification}` : null;
  const tellButton = <button type="button" className="av-after-linkbtn" onClick={() => void tell()}>{w("questions.tell", { party: party || w("questions.tell_party") })}</button>;
  const options = classes.includes(classification) || !classification ? classes : [...classes, classification];

  return (
    <li className="av-stack" id={`question-${question.number}`} hidden={!shown}>
      <div className="av-after-qcard av-stack">
        <div className="av-after-qcard__tags">
          <strong className="av-muted">{w("questions.number", { number: question.number })}</strong>
          {classification && <span className={`av-after-pillchip${noteKey ? " av-after-pillchip--flag" : ""}`}>{keyName("classification", classification)}</span>}
          {!question.classification && question.suggested_classification && <small className="av-muted">{w("questions.suggested")}</small>}
          <button type="button" className="av-after-linkbtn" aria-expanded={changing} onClick={() => setChanging(!changing)}>{w("questions.change")}</button>
        </div>
        {changing && (
          <Select
            label={w("questions.classification")}
            value={classification}
            onChange={(e) => { setClassification(e.target.value); void patch({ classification: e.target.value }); }}
          >
            {!classification && <option value="" />}
            {options.map((c) => <option key={c} value={c}>{keyName("classification", c)}</option>)}
          </Select>
        )}
        <h3 className="av-after-qtext">{question.text_verbatim}</h3>
        {question.para_ref && (
          <p className="av-after-about"><span className="av-muted">{w("questions.about_label")}</span> <span className="av-after-aboutchip">{w("questions.about_paras", { para: question.para_ref })}</span>{(question.page_nos ?? []).map((n) => <PageRef key={n} page={n} />)}</p>
        )}
      </div>
      <DictationBox bar caseId={caseId} context="written_question_answer" label={w("questions.answer")} value={answer} onChange={(v) => { setAnswer(v); setSaved(false); }} rows={6} />
      {error && <Notice tone="error"><p>{error}</p></Notice>}
      {saved && <p role="status">{w("questions.saved")}</p>}
      {noteKey && (
        <div role="note" aria-label={w("questions.beyond_title", { number: question.number })} className="av-after-note">
          <WarnIcon />
          <span className="av-after-note__text"><strong>{w("questions.beyond_title", { number: question.number })}:</strong> {t(noteKey)}</span>
          {tellButton}
        </div>
      )}
      {note && <Notice><h5>{w("questions.tell_title")}</h5><p>{t(note.key, note.params as Record<string, string | number>)}</p></Notice>}
      {told && !note && <p className="av-muted">{w("questions.told", { date: longDate(told, locale) })}</p>}
      <div className="av-after-footer">
        {!noteKey && tellButton}
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
  const [open, setOpen] = useState(false);
  if (!open) return <div><button type="button" className="av-after-linkbtn" onClick={() => setOpen(true)}>{w("questions.add")}</button></div>;
  return (
    <section className="av-card av-stack" aria-label={w("questions.add")}>
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
