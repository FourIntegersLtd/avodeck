// S06 What you've been asked: each question and assumption in the letter's own words (rule 1: the AI only
// finds them). The expert confirms each, may reword one (the letter's words stay on show), sets the kind of
// question from the pack, flags a question outside their field (with a note to draft), or confirms all.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { humanise, t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Checkbox, LinkButton, PageViewer, LoadError, Loading, Notice, QChip, StaleChoice, TextLink, useBusy } from "../../foundation/ui";
import { TextField } from "../../foundation/ui";
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
  const party = pack.instructing_party_types[0]?.label ?? "";
  const kinds: Option[] = pack.question_types.map((q) => ({ value: String(q.key), label: t(String(q.label_key ?? ""), {}, humanise(String(q.key))) }));
  const { total, confirmed_count: done } = asked;
  const path = { params: { path: { case_id: caseId } } };
  const [adding, setAdding] = useState(false);
  const goToPage = (n: number | null | undefined) => {
    if (!n) return;
    const el = document.getElementById(`letter-page-${n}`);
    const pane = el?.closest<HTMLElement>("[data-letter-pane]");
    if (el && pane) pane.scrollTo?.({ top: el.offsetTop - pane.offsetTop, behavior: "smooth" });
  };
  const empty = total === 0 && !asked.reading && !asked.read_failed;

  const confirmAll = async () => {
    setError(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/questions/confirm-all", path));
      reload();
    } catch (err) {
      if (err instanceof ApiError && err.code === "stale") reload();
      setError(errorMessage(err, { stale: "setup.questions.changed", letter_not_read: "setup.questions.letter_not_read" }));
    }
  };
  const readAgain = async () => {
    setError(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/questions/read-again", path));
      reload();
    } catch (err) {
      setError(errorMessage(err));
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
    <div className="av-page av-page--wide av-stack">
      <div className="av-row av-row--spread av-setup-title">
        <div>
          <h1>{t("setup.questions.title")}</h1>
          <p className="av-muted">{t("setup.questions.intro")}</p>
        </div>
        {total > 0 && <strong className="av-setup-count">{t("setup.questions.progress", { done, total })}</strong>}
      </div>
      {asked.warnings.map((key) => (
        <Notice key={key} tone="warning">
          <p>{t(key)}</p>
          {key === "setup.questions.warning.rejected" && (
            <Button variant="quiet" onClick={() => goToPage(1)}>
              {t("setup.questions.read_letter")}
            </Button>
          )}
        </Notice>
      ))}
      {asked.read_failed && (
        <div>
          <Button onClick={() => readAgain()}>{t("setup.questions.read_again")}</Button>
        </div>
      )}
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
      <div className="av-setup-split">
      <LetterPane caseId={caseId} versionId={asked.instruction_version_id ?? null} />
      <div className="av-stack">
      {empty && (
        <Notice>
          <p>{t("setup.questions.no_questions")}</p>
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
          onSeen={() => goToPage(q.page)}
          onChange={(next) => {
            setQuestions(questions.map((x, j) => (j === i ? (next as QuestionItem) : x)));
            reload();
          }}
          pack={pack}
          party={party}
          onDraft={() => draft(q.id)}
          extra={(save, busy) => (
            <>
              <Select label={t("setup.questions.kind")} options={kinds} value={q.kind ?? ""} aria-busy={busy || undefined} disabled={asked.locked || busy} onChange={(e) => void save({ kind: e.target.value })} />
              <Checkbox
                label={t("setup.questions.outside")}
                checked={q.outside_expertise}
                aria-busy={busy || undefined}
                disabled={asked.locked || busy}
                onChange={(e) => void save({ outside_expertise: e.target.checked })}
              />
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
          onChange={(next) => {
            setAssumptions(assumptions.map((x, j) => (j === i ? (next as AssumptionItem) : x)));
            reload();
          }}
        />
      ))}
      {note && <NoteView note={note} />}
      {!asked.locked && (
        <AddQuestion
          open={adding}
          onOpen={() => setAdding(true)}
          onClose={() => setAdding(false)}
          caseId={caseId}
          kinds={kinds}
          onAdded={() => {
            setAdding(false);
            reload();
          }}
        />
      )}
      </div>
      </div>
      {total > 0 && (
        <div className="av-row av-row--spread av-setup-footer">
          {asked.locked ? (
            <LinkButton arrow to={`/cases/${caseId}/preparing`}>{t("setup.questions.continue")}</LinkButton>
          ) : (
            <>
              <span className="av-setup-footer__hint">
                {total > done ? t("setup.questions.last", { count: total - done }) : t("setup.questions.ready")} · <TextLink to={`/cases/${caseId}/details`}>{t("setup.questions.further")}</TextLink>
              </span>
              <Button arrow disabled={done < total} onClick={() => confirmAll()}>
                {t("setup.questions.confirm_all")}
              </Button>
            </>
          )}
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
  pack,
  party,
  onDraft,
  onSeen,
}: {
  onSeen?: () => void;
  pack?: Schemas["CasePack"];
  party?: string;
  onDraft?: () => void | Promise<void>;
  kind: "question" | "assumption";
  caseId: string;
  item: QuestionItem | AssumptionItem;
  locked: boolean;
  onChange: (next: QuestionItem | AssumptionItem) => void;
  extra?: (save: (change: Change) => Promise<void | undefined>, busy: boolean) => ReactNode;
}) {
  const [editing, setEditing] = useState(false);
  const [wording, setWording] = useState(item.confirmed_text ?? item.text_verbatim);
  const [error, setError] = useState<string | null>(null);
  const headingId = `setup-${kind}-${item.id}`;

  const [pending, setPending] = useState<Change>({});
  const [stale, setStale] = useState<QuestionItem | AssumptionItem | null>(null);
  const wordsOf = (i: QuestionItem | AssumptionItem) => i.confirmed_text ?? i.text_verbatim;
  const saving = useBusy();
  const save = (change: Change, version = item.version) => saving.run(() => send(change, version));
  const send = async (change: Change, version: number) => {
    setError(null);
    try {
      const headers = ifMatch(versionTag(version));
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
      setStale(null);
    } catch (err) {
      if (err instanceof ApiError && err.code === "stale") {
        // Fetch what is saved now, so the expert can choose; their wording stays in the box.
        try {
          const latest = await unwrap(api.GET("/v1/cases/{case_id}/questions", { params: { path: { case_id: caseId } } }));
          const found = [...latest.questions, ...latest.assumptions].find((x) => x.id === item.id);
          if (found) {
            setPending(change);
            setStale(found);
            return;
          }
        } catch {
          /* fall through to the plain message */
        }
      }
      setError(errorMessage(err, { locked: "setup.questions.locked" }));
    }
  };
  const reworded = item.confirmed_text && item.confirmed_text !== item.text_verbatim;
  const outside = kind === "question" && (item as QuestionItem).outside_expertise;

  return (
    <article aria-labelledby={headingId} onFocus={onSeen} onClick={onSeen} className={`av-card av-stack${outside ? " av-setup-card--outside" : ""}`}>
      <div className="av-row av-row--spread">
        <div className="av-row">
          <h4 id={headingId} className="av-visually-hidden">
            {t(`setup.questions.${kind}_number`, { number: item.number })}
          </h4>
          {kind === "question" ? (
            <>
              <span aria-hidden="true">
                <QChip n={item.number} />
              </span>
              <KindPill kind={(item as QuestionItem).kind} pack={pack} />
            </>
          ) : (
            <strong>{t("setup.questions.assumption_label")}</strong>
          )}
        </div>
        {item.status === "confirmed" && (
          <strong className="av-setup-confirmed">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
              <path d="M5 12l5 5 9-10" />
            </svg>
            {t("setup.questions.confirmed")}
          </strong>
        )}
      </div>
      {reworded ? (
        <>
          <p className="av-setup-text">{item.confirmed_text}</p>
          <p className="av-small av-muted">{t("setup.questions.letter_says")}</p>
          <p className="av-setup-quote av-muted">{item.text_verbatim}</p>
        </>
      ) : (
        <p className="av-setup-text">{item.text_verbatim}</p>
      )}
      {kind === "question" && (item as QuestionItem).source === "expert" && <p className="av-small av-muted">{t("setup.questions.you_added")}</p>}
      {item.para_ref && <p className="av-mono av-muted av-setup-ref">{t("setup.questions.from_paragraph", { ref: item.para_ref })}</p>}
      {outside && <OutsideExpertise question={item as QuestionItem} pack={pack} locked={locked} party={party} onAnswer={() => save({ confirm: true })} onDraft={onDraft} />}
      {editing && (
        <form
          className="av-stack"
          onSubmit={(e) => {
            e.preventDefault();
            void save({ text: wording.trim() });
          }}
        >
          <TextArea label={t("setup.questions.your_wording")} rows={3} value={wording} onChange={(e) => setWording(e.target.value)} />
          {extra?.(save, saving.busy)}
          <div className="av-row">
            <Button type="submit" variant="secondary" busy={saving.busy} disabled={!wording.trim()}>
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
          {item.status !== "confirmed" && <Button variant="secondary" onClick={() => save({ confirm: true })}>{t("setup.questions.confirm")}</Button>}
          <Button variant="quiet" onClick={() => setEditing(true)}>
            {t("setup.questions.change_wording")}
          </Button>
        </div>
      )}
      {stale && (
        <StaleChoice
          draft={pending.text ?? (pending.confirm ? t("setup.questions.confirm") : pending.kind ? humanise(pending.kind) : wordsOf(item))}
          saved={wordsOf(stale)}
          savedVersion={stale.version}
          onKeepMine={(version) => save(pending, version)}
          onUseSaved={() => {
            onChange(stale);
            setEditing(false);
            setStale(null);
          }}
          onClose={() => setStale(null)}
        />
      )}
    </article>
  );
}

/** The kind of question as a quiet pill, in the pack's word. */
function KindPill({ kind, pack }: { kind: string | null | undefined; pack?: Schemas["CasePack"] }) {
  const type = pack?.question_types.find((q) => q.key === kind);
  if (!kind) return null;
  return (
    <span className="av-setup-kind" data-testid="kind-pill">
      {t(String(type?.label_key ?? ""), {}, humanise(kind))}
    </span>
  );
}

/** Why a question may be outside the expert's field, and the parts the pack says the expert still answers. */
function OutsideExpertise({
  question,
  pack,
  locked,
  party,
  onAnswer,
  onDraft,
}: {
  question: QuestionItem;
  pack?: Schemas["CasePack"];
  locked: boolean;
  party?: string | undefined;
  onAnswer: () => void | Promise<unknown>;
  onDraft?: (() => void | Promise<void>) | undefined;
}) {
  const type = (pack?.question_types as { key: string; parts?: { key: string; label_key?: string }[] }[] | undefined)?.find((q) => q.key === question.kind);
  const parts = (question.answer_parts ?? [])
    .map((key) => {
      const part = type?.parts?.find((p) => p.key === key);
      return t(String(part?.label_key ?? ""), {}, humanise(key)).toLowerCase();
    })
    .join(` ${t("setup.questions.and")} `);
  return (
    <>
      <div className="av-setup-expertise">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" aria-hidden="true" focusable="false">
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7v6M12 16.5v.5" />
        </svg>
        <p>
          <strong>{question.outside_expertise_source === "expert" ? t("setup.questions.expertise_expert") : t("setup.questions.expertise_pack")}</strong>
          {parts && <> {t("setup.questions.expertise_parts", { parts })}</>}
        </p>
      </div>
      {!locked && (
        <div className="av-row">
          {parts && question.status !== "confirmed" && (
            <Button variant="secondary" onClick={onAnswer}>
              {t("setup.questions.answer_only", { parts })}
            </Button>
          )}
          {onDraft && (
            <Button variant="secondary" onClick={onDraft}>
              {party ? t("setup.questions.draft_note_to", { party }) : t("setup.questions.draft_note")}
            </Button>
          )}
        </div>
      )}
    </>
  );
}

/** The expert's own question, for one the reading missed: it still has to be confirmed like the rest. */
function AddQuestion({ open, onOpen, onClose, caseId, kinds, onAdded }: { open: boolean; onOpen: () => void; onClose: () => void; caseId: string; kinds: Option[]; onAdded: () => void }) {
  const [text, setText] = useState("");
  const [para, setPara] = useState("");
  const [kind, setKind] = useState(kinds[0]?.value ?? "");
  const [error, setError] = useState<string | null>(null);
  const saving = useBusy();
  if (!open) {
    return (
      <div>
        <Button variant="secondary" onClick={onOpen}>
          {t("setup.questions.add_question")}
        </Button>
      </div>
    );
  }
  const submit = () =>
    saving.run(async () => {
      setError(null);
      try {
        await unwrap(api.POST("/v1/cases/{case_id}/questions", { params: { path: { case_id: caseId } }, body: { text: text.trim(), para_ref: para.trim() || null, kind } }));
        onAdded();
      } catch (err) {
        setError(errorMessage(err));
      }
    });
  return (
    <form
      className="av-card av-stack"
      aria-label={t("setup.questions.add_question")}
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <TextArea label={t("setup.questions.add_question_text")} rows={3} value={text} onChange={(e) => setText(e.target.value)} />
      <TextField name="para_ref" label={t("setup.questions.add_question_para")} placeholder="4(a)" value={para} onChange={(e) => setPara(e.target.value)} />
      <Select label={t("setup.questions.kind")} options={kinds} value={kind} onChange={(e) => setKind(e.target.value)} />
      {error && (
        <Notice tone="error">
          <p>{error}</p>
        </Notice>
      )}
      <div className="av-row">
        <Button type="submit" busy={saving.busy} disabled={!text.trim()}>
          {t("setup.questions.add_question_submit")}
        </Button>
        <Button variant="quiet" onClick={onClose}>
          {t("setup.cancel")}
        </Button>
      </div>
    </form>
  );
}

/** The letter itself, page by page as pictures, so the expert reads it while checking the questions. */
function LetterPane({ caseId, versionId }: { caseId: string; versionId: string | null }) {
  const [urls, setUrls] = useState<(string | null)[]>([]);
  const made = useRef<string[]>([]);
  useEffect(() => {
    if (!versionId) return;
    let cancelled = false;
    const load = async () => {
      try {
        const { page_count } = await unwrap(api.GET("/v1/cases/{case_id}/instructions/{version_id}/pages", { params: { path: { case_id: caseId, version_id: versionId } } }));
        setUrls(Array.from({ length: page_count }, () => null));
        for (let n = 1; n <= page_count; n++) {
          const blob = await unwrap(api.GET("/v1/cases/{case_id}/instructions/{version_id}/pages/{page_number}", { params: { path: { case_id: caseId, version_id: versionId, page_number: n } }, parseAs: "blob" }));
          if (cancelled) return;
          const url = URL.createObjectURL(blob as Blob);
          made.current.push(url);
          setUrls((now) => now.map((u, i) => (i === n - 1 ? url : u)));
        }
      } catch {
        /* the questions still work without the pictures */
      }
    };
    void load();
    return () => {
      cancelled = true;
      made.current.forEach((u) => URL.revokeObjectURL(u));
      made.current = [];
    };
  }, [caseId, versionId]);
  if (urls.length === 0) return null;
  return (
    <section aria-label={t("setup.questions.letter_title")} data-letter-pane className="av-setup-letter av-stack">
      <span className="av-mono av-setup-letter__head">{t("setup.questions.letter_title")}</span>
      <PageViewer
        pages={urls.map((src, i) => ({
          src,
          id: `letter-page-${i + 1}`,
          label: t("setup.questions.letter_page", { page: i + 1 }),
          children: <span className="av-setup-letter__caption">{t("setup.questions.letter_page", { page: i + 1 })}</span>,
        }))}
      />
    </section>
  );
}
