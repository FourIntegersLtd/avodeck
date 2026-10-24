// S24 Trial pack (docs/design/screens/S24-TrialPack.dc.html): choose what goes in, see a readiness sheet as
// the court will, make the pack from the served report on the server, download or print it behind the code,
// check a new note against what was served, and put a sealed read-only copy on this device for the witness
// box (made, put there and removed behind the code). The note check and the copy open behind one link each.
import { useEffect, useId, useRef, useState } from "react";
import { humanise, t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { screenPath } from "../../foundation/lib/screens";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { BackLink, Button, Callout, LinkButton, LoadError, Loading, Notice, PageRef, TextField } from "../../foundation/ui";
import { longDate, num, partyWord, printBlob, saveBlob, tag, trialRef, vocab } from "./data";
import { TrialFrame, TrialPage, TrialTitle, type Frame } from "./frame";
import { forgetCopy, hasCopy, PIN_PATTERN, saveCopy } from "./offline";
import { w, wn, type TrialKey } from "./words";

type Section = Schemas["TrialPackSettingsIn"]["sections"][number];
type Message = { tone: "success" | "error" | "info"; text: string } | null;

const POLL_MS = 800;
const SECTIONS: Section[] = ["report", "chronology", "readiness", "joint_statement", "written_answers", "legal_tests"];
const ERRORS = {
  not_served: "trial.pack.error.not_served",
  precondition_failed: "trial.pack.error.stale",
  stale: "trial.pack.error.stale",
  already_downloaded: "trial.pack.offline.gone",
  revoked: "trial.pack.offline.gone_revoked",
  expired: "trial.pack.offline.gone_expired",
};

export function TrialPack() {
  return <TrialFrame>{(frame) => <Pack frame={frame} />}</TrialFrame>;
}

function Pack({ frame }: { frame: Frame }) {
  const path = { case_id: frame.caseId };
  // The preview is a help: when it can't be had, the rest of the screen still works.
  const preview = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/trial-pack/preview", { params: { path } })).catch(() => null), [frame.caseId]);
  const latest = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/trial-pack/latest", { params: { path } })), [frame.caseId], { cache: "trial/TrialPack#Pack:latest" });
  const sheets = preview.data?.sheets ?? [];
  const caseLabel = preview.data?.case_label;
  const totalPages = preview.data?.total_pages ?? 0;
  const made = latest.data?.pack;
  return (
    <TrialPage width="s24">
      <div className="av-trial-head__text">
        <BackLink to={screenPath("court_bundle_pages", { caseId: frame.caseId }) ?? `/cases/${frame.caseId}/trial-pages`}>{w("pack.back")}</BackLink>
        <TrialTitle title={w("pack.title")}>
          <p className="av-trial-lead">{[w("pack.lead"), made && w("pack.built", { date: longDate(made.created_at, frame.locale) })].filter(Boolean).join(" ")}</p>
        </TrialTitle>
      </div>
      <Contents
        frame={frame}
        latest={latest}
        pages={preview.data?.sections ?? []}
        onChanged={preview.reload}
        preview={sheets.length > 0 ? <Preview frame={frame} sheets={sheets} bundleName={vocab(frame.pack, "court_bundle")} caseLabel={caseLabel} totalPages={totalPages} /> : null}
      />
    </TrialPage>
  );
}

const PRINTER = (
  <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round">
    <path d="M7 9V3h10v6M7 17H5a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2" />
    <rect x="7" y="14" width="10" height="7" rx="1" />
  </svg>
);

function Preview({ frame, sheets, bundleName, caseLabel, totalPages }: { frame: Frame; sheets: Schemas["SheetOut"][]; bundleName: string; caseLabel: string | null | undefined; totalPages: number }) {
  const id = useId();
  const [at, setAt] = useState(0);
  const sheet = sheets[Math.min(at, sheets.length - 1)]!;
  return (
    <section aria-labelledby={id} className="av-trial-preview av-stack">
      <h2 id={id} className="av-visually-hidden">{w("pack.preview.heading")}</h2>
      <div className="av-trial-preview__bar">
        <p className="av-trial-strong av-trial-muted">{w("pack.preview.sheet", { n: at + 1, of: sheets.length })}</p>
        <div className="av-row">
          <Button variant="secondary" className="av-trial-arrowbtn" aria-label={w("pack.preview.previous")} onClick={() => setAt(at - 1)} disabled={at === 0}>‹</Button>
          <Button variant="secondary" className="av-trial-arrowbtn" aria-label={w("pack.preview.next")} onClick={() => setAt(at + 1)} disabled={at >= sheets.length - 1}>›</Button>
        </div>
      </div>
      <Sheet key={sheet.number} frame={frame} sheet={sheet} bundleName={bundleName} caseLabel={caseLabel} totalPages={totalPages} />
    </section>
  );
}

/** One readiness sheet as the court sees it: the question, my answer, what I may be asked and my reply. */
function Sheet({ frame, sheet, bundleName, caseLabel, totalPages }: { frame: Frame; sheet: Schemas["SheetOut"]; bundleName: string; caseLabel: string | null | undefined; totalPages: number }) {
  const id = useId();
  const [reply, setReply] = useState(sheet.reply ?? "");
  const [draft, setDraft] = useState(sheet.reply ?? "");
  const [at, setAt] = useState<string | null>(sheet.replied_at ?? null);
  const [editing, setEditing] = useState(false);
  const [message, setMessage] = useState<Message>(null);
  const canWrite = frame.can("opinion.write");

  const save = async () => {
    setMessage(null);
    try {
      const done = await unwrap(
        api.PUT("/v1/cases/{case_id}/readiness-replies/{question_no}", { params: { path: { case_id: frame.caseId, question_no: sheet.number } }, body: { reply: draft.trim() } }),
      );
      setReply(draft.trim());
      setAt(done.replied_at);
      setEditing(false);
      setMessage({ tone: "success", text: w("pack.saved") });
    } catch (error) {
      setMessage({ tone: "error", text: errorMessage(error, ERRORS) });
    }
  };

  return (
    <article className="av-trial-sheet av-stack">
      <header className="av-trial-sheet__head">
        <h3>{w("pack.sheet.title", { number: sheet.number, text: sheet.text })}</h3>
        <p className="av-trial-sheet__pages">
          {sheet.pages.map((p) => (
            <span key={p.page_no}>
              {p.trial_page_no !== null && p.status !== "missing" && <><span>{trialRef(bundleName, p.trial_page_no, frame.locale)}</span> · </>}
              <PageRef page={p.page_no} />
              {(p.status === "missing" || p.trial_page_no === null) && <span className="av-trial-sheet__flag">{w("pack.sheet.not_in_bundle", { court_bundle: bundleName })}</span>}
            </span>
          ))}
        </p>
      </header>
      {sheet.answer && (
        <div>
          <p className="av-trial-sheet__label">{w("pack.sheet.my_answer")}</p>
          <p className="av-trial-prose">{sheet.answer}</p>
        </div>
      )}
      <div>
        <p className="av-trial-sheet__label">{w("pack.sheet.asked")}</p>
        <table className="av-trial-sheet__table">
          <thead>
            <tr>
              <th scope="col">{w("pack.sheet.col_against")}</th>
              <th scope="col">{w("pack.sheet.col_reply")}</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>
                {sheet.asked}
                {sheet.agreed === false && sheet.reason && <span className="av-trial-sheet__reason">{w("pack.sheet.reason", { reason: sheet.reason })}</span>}
              </td>
              <td>
                {reply}
                {reply && at && <span className="av-trial-sheet__reason">{w("pack.sheet.replied", { date: longDate(at, frame.locale) })}</span>}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      {message && <Notice tone={message.tone}><p>{message.text}</p></Notice>}
      {canWrite && !editing && (
        <button type="button" className="av-text-link av-trial-linkbutton" onClick={() => { setDraft(reply); setEditing(true); }}>
          {reply ? w("pack.sheet.reply_edit") : w("pack.sheet.reply_write")}
        </button>
      )}
      {canWrite && editing && (
        <div className="av-field">
          <label className="av-field__label" htmlFor={id}>{w("pack.sheet.my_reply")}</label>
          <textarea id={id} className="av-textarea av-trial-short" value={draft} onChange={(e) => setDraft(e.target.value)} />
          <div className="av-trial-actions">
            <Button variant="quiet" onClick={() => setEditing(false)}>{w("meeting.item.close")}</Button>
            <Button variant="secondary" onClick={save} disabled={!draft.trim()}>{w("pack.sheet.reply_save")}</Button>
          </div>
        </div>
      )}
      {caseLabel && sheet.pack_page_no != null && totalPages > 0 && (
        <p className="av-trial-sheet__footer">{w("pack.sheet.footer", { case: caseLabel, page: sheet.pack_page_no, pages: totalPages })}</p>
      )}
    </article>
  );
}

function sectionLabel(section: Section, frame: Frame): string {
  if (section === "joint_statement") return w("pack.section.joint_statement", { document: vocab(frame.pack, "expert_meeting_document") });
  return w(`pack.section.${section}` as TrialKey);
}

function sameSet(a: Set<Section>, b: Set<Section>) {
  return a.size === b.size && [...a].every((x) => b.has(x));
}

function Contents({ frame, latest, pages, onChanged, preview }: { frame: Frame; latest: ReturnType<typeof useResource<Schemas["LatestOut"]>>; pages: Schemas["SectionPages"][]; onChanged: () => void; preview: React.ReactNode }) {
  const path = { case_id: frame.caseId };
  const stepUp = useStepUp();
  const settings = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/trial-pack/settings", { params: { path } })), [frame.caseId]);
  const [chosen, setChosen] = useState<Set<Section> | null>(null);
  const [guidance, setGuidance] = useState<boolean | null>(null);
  const [base, setBase] = useState<{ sections: Set<Section>; guidance: boolean } | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [message, setMessage] = useState<Message>(null);
  const [job, setJob] = useState<string | null>(null);
  const [changedSince, setChangedSince] = useState(false);
  const [copyOpen, setCopyOpen] = useState(false);
  const reloadLatest = useRef(latest.reload);
  reloadLatest.current = latest.reload;
  const changed = useRef(onChanged);
  changed.current = onChanged;

  useEffect(() => {
    if (!settings.data) return;
    const sections = new Set(settings.data.sections as Section[]);
    setChosen(sections);
    setGuidance(settings.data.include_guidance);
    setBase({ sections, guidance: settings.data.include_guidance });
    setVersion(settings.data.version);
  }, [settings.data]);

  useEffect(() => {
    if (!job) return;
    let live = true;
    const timer = window.setInterval(async () => {
      try {
        const state = await unwrap(api.GET("/v1/jobs/{job_id}", { params: { path: { job_id: job } } }));
        if (!live || (state.status !== "done" && state.status !== "failed")) return;
        setJob(null);
        if (state.status === "failed") setMessage({ tone: "error", text: w("pack.failed") });
        else setChangedSince(false);
        reloadLatest.current();
        changed.current();
      } catch (error) {
        if (!live) return;
        setJob(null);
        setMessage({ tone: "error", text: errorMessage(error, ERRORS) });
      }
    }, POLL_MS);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [job]);

  if (settings.error || latest.error) return <LoadError onRetry={() => { settings.reload(); latest.reload(); }} />;
  if (!chosen || !base || version === null || !latest.data) return <Loading />;

  const fail = (error: unknown) => setMessage({ tone: "error", text: errorMessage(error, ERRORS) });
  const toggle = (section: Section) =>
    setChosen((current) => {
      const next = new Set(current);
      if (next.has(section)) next.delete(section);
      else next.add(section);
      return next;
    });
  const dirty = !sameSet(chosen, base.sections) || (guidance ?? false) !== base.guidance;

  const save = async () => {
    setMessage(null);
    try {
      const next = await unwrap(
        api.PUT("/v1/cases/{case_id}/trial-pack/settings", {
          params: { path },
          headers: ifMatch(tag(version)),
          body: { sections: SECTIONS.filter((s) => chosen.has(s)), include_guidance: guidance ?? false },
        }),
      );
      setVersion(next.version);
      setBase({ sections: new Set(chosen), guidance: guidance ?? false });
      setChangedSince(true);
      setMessage({ tone: "success", text: w("pack.saved") });
      onChanged();
    } catch (error) {
      fail(error);
      settings.reload();
    }
  };

  const build = async () => {
    setMessage(null);
    try {
      const queued = await unwrap(api.POST("/v1/cases/{case_id}/trial-pack", { params: { path } }));
      setJob(queued.job_id);
    } catch (error) {
      fail(error);
    }
  };

  const download = async () => {
    setMessage(null);
    try {
      const blob = await stepUp(() => unwrap(api.GET("/v1/cases/{case_id}/trial-pack/latest/download", { params: { path }, parseAs: "blob" })));
      if (blob) saveBlob(blob as Blob, "trial-pack.pdf");
    } catch (error) {
      fail(error);
    }
  };

  const print = async () => {
    setMessage(null);
    try {
      const blob = await stepUp(() => unwrap(api.GET("/v1/cases/{case_id}/trial-pack/latest/download", { params: { path }, parseAs: "blob" })));
      if (blob) printBlob(blob as Blob);
    } catch (error) {
      fail(error);
    }
  };

  const countOf = (key: string) => pages.find((p) => p.key === key)?.pages;
  const pack = latest.data.pack;
  const canBuild = frame.can("report.build");
  const canGet = frame.can("case.view_content");
  const rebuild = (
    <Button variant="secondary" onClick={build} disabled={job !== null}>{w("pack.rebuild")}</Button>
  );
  return (
    <>
      {message && <Notice tone={message.tone}><p>{message.text}</p></Notice>}
      <div className="av-trial-split">
        <div className="av-stack">
          <section aria-labelledby="trial-what-goes-in" className="av-card av-trial-checklist">
            <h2 id="trial-what-goes-in" className="av-trial-checklist__head">{w("pack.sections.heading")}</h2>
            {SECTIONS.map((s) => (
              <Choice key={s} label={sectionLabel(s, frame)} hint={w(`pack.section.${s}_hint` as TrialKey)} pages={countOf(s)} checked={chosen.has(s)} onChange={() => toggle(s)} disabled={!canBuild} />
            ))}
            <Choice label={w("pack.section.guidance")} hint={w("pack.section.guidance_hint")} pages={countOf("guidance")} checked={guidance ?? false} onChange={() => setGuidance((g) => !g)} disabled={!canBuild} />
            {canBuild && dirty && (
              <div className="av-trial-checklist__save">
                <Button variant="secondary" onClick={save}>{w("pack.save")}</Button>
              </div>
            )}
          </section>
          <Check frame={frame} />
        </div>
        {preview}
      </div>

      {pack?.stale && (
        <Callout
          title={w("pack.stale")}
          actions={canBuild ? rebuild : undefined}
        >
          <p>{staleReason(pack.stale_reason, frame)}</p>
        </Callout>
      )}
      {job && <Notice><p>{w("pack.building")}</p></Notice>}

      <div className="av-trial-bar">
        <p className="av-trial-muted av-trial-bar__summary">
          {pack ? w("pack.summary", { pages: num(pack.page_count ?? 0, frame.locale) }) : !job && w("pack.none")}
        </p>
        <div className="av-trial-actions">
          {pack && changedSince && !pack.stale && canBuild && rebuild}
          {pack && canGet && <Button variant="secondary" onClick={download}>{w("pack.download")}</Button>}
          {pack && canGet && <Button className="av-trial-icon-button" onClick={print}>{PRINTER}{w("pack.print")}</Button>}
          {!pack && canBuild && <Button onClick={build} disabled={job !== null}>{w("pack.build")}</Button>}
        </div>
      </div>
      {frame.can("report.sign") && (
        <div className="av-stack">
          <button type="button" className="av-text-link av-trial-linkbutton" aria-expanded={copyOpen} onClick={() => setCopyOpen((o) => !o)}>{w("pack.offline.show")}</button>
          {copyOpen && <OfflineCopy frame={frame} />}
        </div>
      )}
    </>
  );
}

function Choice({ label, hint, pages, checked, onChange, disabled }: { label: string; hint: string; pages?: number; checked: boolean; onChange: () => void; disabled?: boolean }) {
  const id = useId();
  return (
    <div className="av-trial-choice-row">
      <input id={`${id}-box`} type="checkbox" checked={checked} onChange={onChange} disabled={disabled} aria-describedby={`${id}-hint`} />
      <div>
        <label htmlFor={`${id}-box`} className="av-trial-strong av-trial-choice-row__label">{label}</label>
        <p id={`${id}-hint`} className="av-trial-muted av-trial-hint">{hint}</p>
      </div>
      {pages !== undefined && <span className="av-trial-muted av-trial-choice-row__pages">{wn("pack.pages", pages)}</span>}
    </div>
  );
}

/** A note you plan to take into court, checked against what you served. The check opens behind one link. */
function Check({ frame }: { frame: Frame }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [result, setResult] = useState<Schemas["NoteChecked"] | null>(null);
  const [message, setMessage] = useState<Message>(null);
  const party = partyWord(frame);
  const choose = async (noteId: string, choice: "tell" | "leave_out") => {
    setMessage(null);
    try {
      const done = await unwrap(api.PUT("/v1/cases/{case_id}/trial-pack/notes/{note_id}", { params: { path: { case_id: frame.caseId, note_id: noteId } }, body: { choice } }));
      setResult((current) => current && { sentences: current.sentences.map((s) => (s.note_id === noteId ? { ...s, choice: done.choice } : s)) });
    } catch (error) {
      setMessage({ tone: "error", text: errorMessage(error, ERRORS) });
    }
  };
  const run = async () => {
    setMessage(null);
    try {
      setResult(await unwrap(api.POST("/v1/cases/{case_id}/trial-pack/check-new", { params: { path: { case_id: frame.caseId } }, body: { text } })));
    } catch (error) {
      setMessage({ tone: "error", text: errorMessage(error, ERRORS) });
    }
  };
  return (
    <div className="av-stack">
      {!open && (
        <button type="button" className="av-text-link av-trial-linkbutton" aria-expanded={false} onClick={() => setOpen(true)}>{w("pack.check.open")}</button>
      )}
      {open && (
        <section aria-labelledby={`${id}-h`} className="av-card av-stack">
          <h2 id={`${id}-h`} className="av-trial-h3">{w("pack.check.open")}</h2>
          <p>{w("pack.check.lead")}</p>
          <div className="av-field">
            <label className="av-field__label" htmlFor={id}>{w("pack.check.label")}</label>
            <textarea id={id} className="av-textarea av-trial-short" value={text} onChange={(e) => setText(e.target.value)} />
          </div>
          <div className="av-trial-actions">
            <Button variant="quiet" onClick={() => setOpen(false)}>{w("meeting.item.close")}</Button>
            <Button variant="secondary" onClick={run} disabled={!text.trim()}>{w("pack.check.run")}</Button>
          </div>
          {message && <Notice tone="error"><p>{message.text}</p></Notice>}
        </section>
      )}
      {result && (
        <>
          {result.sentences.filter((s) => s.sourced).length > 0 && (
            <ol className="av-trial-sentences">
              {result.sentences.filter((s) => s.sourced).map((s) => (
                <li key={s.index}>
                  <p className="av-trial-prose">{s.text}</p>
                  <p className="av-trial-muted">
                    {s.source?.kind === "report" ? w("pack.check.from_report", { para: s.source.para_no }) : s.source?.kind === "written_answer" ? w("pack.check.from_answer", { question: s.source.question_no }) : ""}
                  </p>
                </li>
              ))}
            </ol>
          )}
          {result.sentences.filter((s) => !s.sourced).map((s) =>
            s.note_id && s.choice ? (
              <p key={s.index} className="av-trial-strong">{s.choice === "tell" ? w("pack.check.told") : w("pack.check.left_out")}</p>
            ) : (
              <Callout
                key={s.index}
                title={w("pack.check.heading")}
                actions={
                  s.note_id && frame.can("report.build") ? (
                    <>
                      <Button variant="secondary" onClick={() => choose(s.note_id!, "tell")}>{w("pack.check.tell", { party })}</Button>
                      <Button variant="quiet" onClick={() => choose(s.note_id!, "leave_out")}>{w("pack.check.leave_out")}</Button>
                    </>
                  ) : undefined
                }
              >
                <p>
                  {w("pack.check.callout_lead")} <em>{w("pack.check.quoted", { text: s.text })}</em> {w("pack.check.callout_tail", { party })}
                </p>
              </Callout>
            ),
          )}
        </>
      )}
    </div>
  );
}

const STALE_REASONS = ["hearing_adjourned", "hearing_changed", "new_pages", "new_served_version"] as const;

/** Why a pack or copy needs making again, in the trial words; the court bundle's name comes from the pack. */
function staleReason(reason: string | null | undefined, frame: Frame): string {
  if (reason === "court_bundle_superseded") return w("pack.stale_reason.court_bundle_superseded", { court_bundle: vocab(frame.pack, "court_bundle") });
  const known = STALE_REASONS.find((r) => r === reason);
  return known ? w(`pack.stale_reason.${known}`) : w("pack.stale_reason.other");
}

function OfflineCopy({ frame }: { frame: Frame }) {
  const id = useId();
  const path = { case_id: frame.caseId };
  const stepUp = useStepUp();
  const list = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/offline-packages", { params: { path } })), [frame.caseId], { cache: "trial/TrialPack#OfflineCopy:list" });
  const [device, setDevice] = useState("");
  const [pin, setPin] = useState("");
  const [pinAgain, setPinAgain] = useState("");
  const [onDevice, setOnDevice] = useState(false);
  const [removed, setRemoved] = useState(false);
  const [message, setMessage] = useState<Message>(null);

  useEffect(() => {
    let live = true;
    void hasCopy(frame.caseId).then((has) => live && setOnDevice(has));
    return () => {
      live = false;
    };
  }, [frame.caseId, list.data]);

  const fail = (error: unknown) => setMessage({ tone: "error", text: errorMessage(error, ERRORS) });
  const kinds = Object.fromEntries(
    frame.pack.fact_kinds.map((k) => String(k.key)).map((key) => [key, t(`kinds.${key}`, {}, humanise(key))]),
  );

  const create = async () => {
    setMessage(null);
    try {
      const made = await stepUp(() =>
        unwrap(api.POST("/v1/cases/{case_id}/offline-packages", { params: { path }, body: { device_label: device.trim(), include_images: false } as never })),
      );
      if (!made) return;
      setRemoved(false);
      list.reload();
    } catch (error) {
      fail(error);
    }
  };

  const download = async (copy: Schemas["PackageOut"]) => {
    setMessage(null);
    try {
      const sealed = await stepUp(() =>
        unwrap(api.GET("/v1/cases/{case_id}/offline-packages/{package_id}/download", { params: { path: { ...path, package_id: copy.id } } })),
      );
      if (!sealed) return;
      await saveCopy(sealed, { label: copy.device_label, locale: frame.locale, expiresAt: copy.expires_at, kinds }, pin);
      setPin("");
      setPinAgain("");
      setOnDevice(true);
      list.reload();
    } catch (error) {
      fail(error);
    }
  };

  const remove = async (copy: Schemas["PackageOut"]) => {
    setMessage(null);
    try {
      const done = await stepUp(async () => {
        await unwrap(api.DELETE("/v1/cases/{case_id}/offline-packages/{package_id}", { params: { path: { ...path, package_id: copy.id } } }));
        return true;
      });
      if (!done) return;
      await forgetCopy(frame.caseId);
      setOnDevice(false);
      setRemoved(true);
      setMessage({ tone: "success", text: w("pack.offline.deleted") });
    } catch (error) {
      fail(error);
    }
  };

  const mismatch = pinAgain !== "" && pinAgain.length >= pin.length && pinAgain !== pin;
  const pinReady = PIN_PATTERN.test(pin) && pin === pinAgain;
  const copies = (list.data?.packages ?? []).filter((c) => c.status === "built" || c.status === "downloaded");
  const shown = removed ? [] : copies;
  return (
    <section aria-labelledby={id} className="av-card av-stack">
      <h2 id={id}>{w("pack.offline.heading")}</h2>
      <p>{w("pack.offline.lead")}</p>
      {list.error && <LoadError onRetry={list.reload} />}
      {message && <Notice tone={message.tone}><p>{message.text}</p></Notice>}
      {shown.map((copy) => {
        const until = longDate(copy.expires_at, frame.locale);
        return (
          <div key={copy.id} className="av-stack">
            <p className="av-trial-strong">{w("pack.offline.copy_line", { device: copy.device_label, date: until })}</p>
            {copy.stale && (
              <Notice tone="warning">
                <p className="av-trial-strong">{w("pack.offline.stale")}</p>
                <p>{staleReason(copy.stale_reason, frame)}</p>
              </Notice>
            )}
            <p>{wn("pack.offline.downloads", copy.status === "built" ? 1 : 0)}</p>
            <p>{w("pack.offline.ends", { date: until })}</p>
            {copy.status === "downloaded" && onDevice && <p>{w("pack.offline.on_device", { date: until })}</p>}
            {copy.status === "downloaded" && !onDevice && <p>{w("pack.offline.gone")}</p>}
            {copy.status === "built" && (
              <>
                <TextField label={w("pack.offline.pin")} hint={w("pack.offline.pin_hint")} name="pin" type="password" inputMode="numeric" autoComplete="new-password" value={pin} onChange={(e) => setPin(e.target.value)} />
                <TextField
                  label={w("pack.offline.pin_again")}
                  name="pin_again"
                  type="password"
                  inputMode="numeric"
                  autoComplete="new-password"
                  value={pinAgain}
                  onChange={(e) => setPinAgain(e.target.value)}
                  error={mismatch ? w("pack.offline.pin_mismatch") : undefined}
                />
              </>
            )}
            <div className="av-trial-actions">
              <Button variant="danger" onClick={() => remove(copy)}>{w("pack.offline.delete")}</Button>
              {copy.status === "built" && <Button onClick={() => download(copy)} disabled={!pinReady}>{w("pack.offline.download")}</Button>}
              {copy.status === "downloaded" && onDevice && (
                <LinkButton to={`/cases/${frame.caseId}/witness-box`}>{w("pack.offline.open")}</LinkButton>
              )}
            </div>
          </div>
        );
      })}
      {list.data && shown.length === 0 && (
        <>
          <TextField label={w("pack.offline.device")} name="device_label" value={device} onChange={(e) => setDevice(e.target.value)} />
          <div className="av-trial-actions">
            <Button onClick={create} disabled={!device.trim()}>{w("pack.offline.create")}</Button>
          </div>
        </>
      )}
    </section>
  );
}
