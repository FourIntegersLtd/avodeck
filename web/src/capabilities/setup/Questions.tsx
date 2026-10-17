// S06 What you've been asked: each question and assumption in the letter's own words (rule 1: the AI only
// finds them). The expert confirms each, may reword one (the letter's words stay on show), sets the kind of
// question from the pack, flags a question outside their field (with a note to draft), or confirms all.
import { useEffect, useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { humanise, t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Checkbox, LoadError, Loading, Notice } from "../../foundation/ui";
import { NoteView, Select, TextArea, versionTag, type Option } from "./shared";

type QuestionItem = Schemas["QuestionOut"];
type AssumptionItem = Schemas["AssumptionOut"];

export function Questions() {
  const { caseId = "" } = useParams();
  const path = { params: { path: { case_id: caseId } } };
  const found = useResource(async () => {
    const [asked, pack] = await Promise.all([unwrap(api.GET("/v1/cases/{case_id}/questions", path)), unwrap(api.GET("/v1/cases/{case_id}/pack", path))]);
    return { asked, pack };
  }, [caseId]);
  const { reload } = found;
  const reading = found.data?.asked.reading ?? false;
  useEffect(() => {
    if (!reading) return;
    const timer = setInterval(reload, 5000);
    return () => clearInterval(timer);
  }, [reading, reload]);
  if (found.error) return <div className="av-page"><LoadError onRetry={found.reload} /></div>;
  if (!found.data) return <div className="av-page"><Loading /></div>;
  return <QuestionsView key={found.data.asked.version_no} caseId={caseId} asked={found.data.asked} pack={found.data.pack} reload={reload} />;
}

function QuestionsView({ caseId, asked, pack, reload }: { caseId: string; asked: Schemas["Questions"]; pack: Schemas["CasePack"]; reload: () => void }) {
  const [questions, setQuestions] = useState(asked.questions);
  const [assumptions, setAssumptions] = useState(asked.assumptions);
  const [note, setNote] = useState<Schemas["NoteDraft"] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setQuestions(asked.questions);
    setAssumptions(asked.assumptions);
  }, [asked]);
  const kinds: Option[] = pack.question_types.map((q) => ({ value: String(q.key), label: t(String(q.label_key ?? ""), {}, humanise(String(q.key))) }));
  const total = questions.length + assumptions.length;
  const done = [...questions, ...assumptions].filter((i) => i.status === "confirmed").length;
  const path = { params: { path: { case_id: caseId } } };

  const confirmAll = async () => {
    setError(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/questions/confirm-all", path));
      reload();
    } catch (err) {
      setError(errorMessage(err, { stale: "setup.stale" }));
    }
  };
  const draft = async (questionId: string) => {
    setError(null);
    try {
      setNote(await unwrap(api.POST("/v1/cases/{case_id}/instructing-party-notes/draft", { ...path, body: { topic: "outside_expertise", question_ids: [questionId] } })));
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <div className="av-page av-page--narrow av-stack">
      <div>
        <h2>{t("setup.questions.title")}</h2>
        <p className="av-muted">{t("setup.questions.intro")}</p>
        {total > 0 && (
          <p>
            <strong>{t("setup.questions.progress", { done, total })}</strong>
          </p>
        )}
      </div>
      {asked.warnings.map((key) => (
        <Notice key={key} tone="warning">
          <p>{t(key)}</p>
        </Notice>
      ))}
      {asked.reading && (
        <Notice>
          <p>{t("setup.questions.reading")}</p>
        </Notice>
      )}
      {asked.locked && (
        <Notice tone="success">
          <p>{t("setup.questions.locked")}</p>
        </Notice>
      )}
      {error && (
        <Notice tone="error">
          <p>{error}</p>
        </Notice>
      )}
      {questions.length > 0 && <h3>{t("setup.questions.questions")}</h3>}
      {questions.map((q, i) => (
        <ItemCard
          key={q.id}
          kind="question"
          caseId={caseId}
          item={q}
          locked={asked.locked}
          onChange={(next) => setQuestions(questions.map((x, j) => (j === i ? (next as QuestionItem) : x)))}
          extra={(save) => (
            <>
              <Select label={t("setup.questions.kind")} options={kinds} value={q.kind ?? ""} disabled={asked.locked} onChange={(e) => void save({ kind: e.target.value })} />
              <Checkbox
                label={t("setup.questions.outside")}
                checked={q.outside_expertise}
                disabled={asked.locked}
                onChange={(e) => void save({ outside_expertise: e.target.checked })}
              />
              {q.outside_expertise && (
                <div>
                  <Button variant="secondary" onClick={() => void draft(q.id)}>
                    {t("setup.questions.draft_note")}
                  </Button>
                </div>
              )}
            </>
          )}
        />
      ))}
      {assumptions.length > 0 && <h3>{t("setup.questions.assumptions")}</h3>}
      {assumptions.map((a, i) => (
        <ItemCard
          key={a.id}
          kind="assumption"
          caseId={caseId}
          item={a}
          locked={asked.locked}
          onChange={(next) => setAssumptions(assumptions.map((x, j) => (j === i ? (next as AssumptionItem) : x)))}
        />
      ))}
      {note && <NoteView note={note} />}
      {!asked.locked && total > 0 && (
        <div className="av-row av-setup-actions">
          <Button onClick={() => void confirmAll()}>{t("setup.questions.confirm_all")}</Button>
        </div>
      )}
    </div>
  );
}

type Change = { text?: string; confirm?: boolean; kind?: string; outside_expertise?: boolean };

function ItemCard({
  kind,
  caseId,
  item,
  locked,
  onChange,
  extra,
}: {
  kind: "question" | "assumption";
  caseId: string;
  item: QuestionItem | AssumptionItem;
  locked: boolean;
  onChange: (next: QuestionItem | AssumptionItem) => void;
  extra?: (save: (change: Change) => Promise<void>) => ReactNode;
}) {
  const [editing, setEditing] = useState(false);
  const [wording, setWording] = useState(item.confirmed_text ?? item.text_verbatim);
  const [error, setError] = useState<string | null>(null);
  const headingId = `setup-${kind}-${item.id}`;

  const save = async (change: Change) => {
    setError(null);
    try {
      const headers = ifMatch(versionTag(item.version));
      const next =
        kind === "question"
          ? await unwrap(api.PATCH("/v1/cases/{case_id}/questions/{question_id}", { params: { path: { case_id: caseId, question_id: item.id } }, body: change, headers }))
          : await unwrap(
              api.PATCH("/v1/cases/{case_id}/assumptions/{assumption_id}", {
                params: { path: { case_id: caseId, assumption_id: item.id } },
                body: { text: change.text, confirm: change.confirm },
                headers,
              }),
            );
      onChange(next);
      setEditing(false);
    } catch (err) {
      setError(errorMessage(err, { stale: "setup.stale", locked: "setup.questions.locked" }));
    }
  };
  const reworded = item.confirmed_text && item.confirmed_text !== item.text_verbatim;

  return (
    <article aria-labelledby={headingId} className="av-card av-stack">
      <div className="av-row av-row--spread">
        <h4 id={headingId}>{t(`setup.questions.${kind}_number`, { number: item.number })}</h4>
        {item.status === "confirmed" && <strong className="av-setup-confirmed">{t("setup.questions.confirmed")}</strong>}
      </div>
      {reworded ? (
        <>
          <p className="av-setup-quote">{item.confirmed_text}</p>
          <p className="av-small av-muted">{t("setup.questions.letter_says")}</p>
          <p className="av-setup-quote av-muted">{item.text_verbatim}</p>
        </>
      ) : (
        <p className="av-setup-quote">{item.text_verbatim}</p>
      )}
      {item.para_ref && <p className="av-small av-muted">{t("setup.questions.from_paragraph", { ref: item.para_ref })}</p>}
      {extra?.(save)}
      {editing && (
        <form
          className="av-stack"
          onSubmit={(e) => {
            e.preventDefault();
            void save({ text: wording.trim() });
          }}
        >
          <TextArea label={t("setup.questions.your_wording")} rows={3} value={wording} onChange={(e) => setWording(e.target.value)} />
          <div className="av-row">
            <Button type="submit" variant="secondary" disabled={!wording.trim()}>
              {t("setup.questions.save_wording")}
            </Button>
            <Button variant="quiet" onClick={() => setEditing(false)}>
              {t("setup.cancel")}
            </Button>
          </div>
        </form>
      )}
      {error && (
        <Notice tone="error">
          <p>{error}</p>
        </Notice>
      )}
      {!locked && !editing && (
        <div className="av-row">
          {item.status !== "confirmed" && <Button variant="secondary" onClick={() => void save({ confirm: true })}>{t("setup.questions.confirm")}</Button>}
          <Button variant="quiet" onClick={() => setEditing(true)}>
            {t("setup.questions.change_wording")}
          </Button>
        </div>
      )}
    </article>
  );
}
