// S24 Trial pack (docs/design/screens/S24-TrialPack.dc.html): choose what goes in, make the pack from the
// served report on the server, download it behind the code, check a new note against what was served, and
// put a sealed read-only copy on this device for the witness box (made, put there and removed behind the code).
import { useEffect, useId, useRef, useState } from "react";
import { humanise, t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { screenPath } from "../../foundation/lib/screens";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { BackLink, Button, LinkButton, LoadError, Loading, Notice, PageRef, TextField } from "../../foundation/ui";
import { longDate, partyWord, printBlob, saveBlob, tag, vocab } from "./data";
import { TrialFrame, TrialHeader, type Frame } from "./frame";
import { forgetCopy, hasCopy, PIN_PATTERN, saveCopy } from "./offline";
import { w, wn, type TrialKey } from "./words";

type Section = Schemas["TrialPackSettingsIn"]["sections"][number];
type Message = { tone: "success" | "error" | "info"; text: string } | null;

const SCREEN = "trial_pack";
const POLL_MS = 800;
const SECTIONS: Section[] = ["report", "chronology", "readiness", "joint_statement", "written_answers", "legal_tests"];
const ERRORS = {
  not_served: "trial.pack.error.not_served",
  precondition_failed: "trial.pack.error.stale",
  stale: "trial.pack.error.stale",
  already_downloaded: "trial.pack.offline.gone",
};

export function TrialPack() {
  return <TrialFrame>{(frame) => <Pack frame={frame} />}</TrialFrame>;
}

function Pack({ frame }: { frame: Frame }) {
  const path = { case_id: frame.caseId };
  // The preview is a help: when it can't be had, the rest of the screen still works.
  const preview = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/trial-pack/preview", { params: { path } })).catch(() => null), [frame.caseId]);
  const sheets = preview.data?.sheets ?? [];
  return (
    <div className="av-page av-stack">
      <TrialHeader pack={frame.pack} screen={SCREEN} title={w("pack.title")} />
      <BackLink to={screenPath("court_bundle_pages", { caseId: frame.caseId }) ?? `/cases/${frame.caseId}/trial-pages`}>{w("pack.back")}</BackLink>
      <p>{w("pack.lead")}</p>
      <div className="av-trial-split">
        <div className="av-stack">
          <Contents frame={frame} pages={preview.data?.sections ?? []} onChanged={preview.reload} />
          <Check frame={frame} />
        </div>
        {sheets.length > 0 && <Preview frame={frame} sheets={sheets} bundleName={vocab(frame.pack, "court_bundle")} />}
      </div>
      {frame.can("report.sign") && <OfflineCopy frame={frame} />}
    </div>
  );
}

function Preview({ frame, sheets, bundleName }: { frame: Frame; sheets: Schemas["SheetOut"][]; bundleName: string }) {
  const id = useId();
  const [at, setAt] = useState(0);
  const sheet = sheets[Math.min(at, sheets.length - 1)]!;
  return (
    <section aria-labelledby={id} className="av-trial-preview av-stack">
      <h2 id={id} className="av-visually-hidden">{w("pack.preview.heading")}</h2>
      <div className="av-trial-preview__bar">
        <p className="av-trial-strong">{w("pack.preview.sheet", { n: at + 1, of: sheets.length })}</p>
        <div className="av-row">
          <Button variant="secondary" aria-label={w("pack.preview.previous")} onClick={() => setAt(at - 1)} disabled={at === 0}>‹</Button>
          <Button variant="secondary" aria-label={w("pack.preview.next")} onClick={() => setAt(at + 1)} disabled={at >= sheets.length - 1}>›</Button>
        </div>
      </div>
      <article className="av-trial-sheet av-stack">
        <header className="av-trial-sheet__head">
          <h3>{w("pack.sheet.title", { number: sheet.number, text: sheet.text })}</h3>
          <p className="av-trial-sheet__pages">
            {sheet.pages.map((p) => (
              <span key={p.page_no}>
                {p.trial_page_no !== null && p.status !== "missing" && <span className="av-mono">{w("pack.sheet.trial_page", { court_bundle: bundleName, page: p.trial_page_no })} · </span>}
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
        {(sheet.asked || sheet.reason) && (
          <div>
            <p className="av-trial-sheet__label">{w("pack.sheet.asked")}</p>
            {sheet.asked && <p className="av-trial-prose">{sheet.asked}</p>}
            {sheet.agreed === false && sheet.reason && <p className="av-trial-prose">{w("pack.sheet.reason", { reason: sheet.reason })}</p>}
          </div>
        )}
        <Reply key={sheet.number} frame={frame} sheet={sheet} />
      </article>
    </section>
  );
}

function Reply({ frame, sheet }: { frame: Frame; sheet: Schemas["SheetOut"] }) {
  const id = useId();
  const [text, setText] = useState(sheet.reply ?? "");
  const [at, setAt] = useState<string | null>(sheet.replied_at ?? null);
  const [message, setMessage] = useState<Message>(null);
  const save = async () => {
    setMessage(null);
    try {
      const done = await unwrap(
        api.PUT("/v1/cases/{case_id}/readiness-replies/{question_no}", { params: { path: { case_id: frame.caseId, question_no: sheet.number } }, body: { reply: text.trim() } }),
      );
      setAt(done.replied_at);
      setMessage({ tone: "success", text: w("pack.saved") });
    } catch (error) {
      setMessage({ tone: "error", text: errorMessage(error, ERRORS) });
    }
  };
  const canWrite = frame.can("opinion.write");
  if (!canWrite && !sheet.reply) return null;
  return (
    <div className="av-field">
      <label className="av-field__label" htmlFor={id}>{w("pack.sheet.my_reply")}</label>
      <textarea id={id} className="av-textarea av-trial-short" value={text} onChange={(e) => setText(e.target.value)} readOnly={!canWrite} />
      {at && <p className="av-trial-muted">{w("pack.sheet.replied", { date: longDate(at, frame.locale) })}</p>}
      {message && <Notice tone={message.tone}><p>{message.text}</p></Notice>}
      {canWrite && (
        <div className="av-trial-actions">
          <Button variant="secondary" onClick={save} disabled={!text.trim()}>{w("pack.sheet.reply_save")}</Button>
        </div>
      )}
    </div>
  );
}

function sectionLabel(section: Section, frame: Frame): string {
  if (section === "joint_statement") return w("pack.section.joint_statement", { document: vocab(frame.pack, "expert_meeting_document") });
  return w(`pack.section.${section}` as TrialKey);
}

function Contents({ frame, pages, onChanged }: { frame: Frame; pages: Schemas["SectionPages"][]; onChanged: () => void }) {
  const path = { case_id: frame.caseId };
  const stepUp = useStepUp();
  const settings = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/trial-pack/settings", { params: { path } })), [frame.caseId]);
  const latest = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/trial-pack/latest", { params: { path } })), [frame.caseId]);
  const [chosen, setChosen] = useState<Set<Section> | null>(null);
  const [guidance, setGuidance] = useState<boolean | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [message, setMessage] = useState<Message>(null);
  const [job, setJob] = useState<string | null>(null);
  const reloadLatest = useRef(latest.reload);
  reloadLatest.current = latest.reload;
  const changed = useRef(onChanged);
  changed.current = onChanged;

  useEffect(() => {
    if (!settings.data) return;
    setChosen(new Set(settings.data.sections as Section[]));
    setGuidance(settings.data.include_guidance);
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
  if (!chosen || version === null || !latest.data) return <Loading />;

  const fail = (error: unknown) => setMessage({ tone: "error", text: errorMessage(error, ERRORS) });
  const toggle = (section: Section) =>
    setChosen((current) => {
      const next = new Set(current);
      if (next.has(section)) next.delete(section);
      else next.add(section);
      return next;
    });

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
  return (
    <>
      {message && <Notice tone={message.tone}><p>{message.text}</p></Notice>}
      <fieldset className="av-card av-stack">
        <legend className="av-trial-legend">{w("pack.sections.heading")}</legend>
        {SECTIONS.map((s) => (
          <Choice key={s} label={sectionLabel(s, frame)} hint={w(`pack.section.${s}_hint` as TrialKey)} pages={countOf(s)} checked={chosen.has(s)} onChange={() => toggle(s)} disabled={!canBuild} />
        ))}
        <Choice label={w("pack.section.guidance")} hint={w("pack.section.guidance_hint")} pages={countOf("guidance")} checked={guidance ?? false} onChange={() => setGuidance((g) => !g)} disabled={!canBuild} />
        {canBuild && (
          <div className="av-trial-actions">
            <Button variant="secondary" onClick={save}>{w("pack.save")}</Button>
          </div>
        )}
      </fieldset>

      <section className="av-card av-stack">
        {job && <Notice><p>{w("pack.building")}</p></Notice>}
        {!job && !pack && <p>{w("pack.none")}</p>}
        {pack?.stale && (
          <Notice tone="warning">
            <p className="av-trial-strong">{w("pack.stale")}</p>
            <p>
              {pack.stale_reason === "court_bundle_superseded"
                ? w("pack.stale_reason.court_bundle_superseded", { court_bundle: vocab(frame.pack, "court_bundle") })
                : w("pack.stale_reason.other")}
            </p>
          </Notice>
        )}
        {pack && !job && (
          <>
            <p>{w("pack.ready", { pages: pack.page_count ?? 0 })}</p>
            <p className="av-trial-muted">{w("pack.built", { date: longDate(pack.created_at, frame.locale) })}</p>
            <p className="av-trial-muted">{w("pack.large_print")}</p>
          </>
        )}
        <div className="av-trial-actions">
          {pack && frame.can("case.view_content") && <Button variant="secondary" onClick={download}>{w("pack.download")}</Button>}
          {pack && frame.can("case.view_content") && <Button variant="secondary" onClick={print}>{w("pack.print")}</Button>}
          {canBuild && <Button onClick={build} disabled={job !== null}>{pack ? w("pack.rebuild") : w("pack.build")}</Button>}
        </div>
      </section>
    </>
  );
}

function Choice({ label, hint, pages, checked, onChange, disabled }: { label: string; hint: string; pages?: number; checked: boolean; onChange: () => void; disabled?: boolean }) {
  const id = useId();
  return (
    <div className="av-trial-choice-row">
      <div className="av-trial-choice-row__main">
        <label className="av-checkbox">
          <input type="checkbox" checked={checked} onChange={onChange} disabled={disabled} aria-describedby={id} />
          <span>{label}</span>
        </label>
        {pages !== undefined && <span className="av-trial-muted">{wn("pack.pages", pages)}</span>}
      </div>
      <p id={id} className="av-trial-muted av-trial-hint">{hint}</p>
    </div>
  );
}

function Check({ frame }: { frame: Frame }) {
  const id = useId();
  const [text, setText] = useState("");
  const [result, setResult] = useState<Schemas["NoteChecked"] | null>(null);
  const [message, setMessage] = useState<Message>(null);
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
    <section aria-labelledby={`${id}-h`} className="av-card av-stack">
      <h2 id={`${id}-h`}>{w("pack.check.heading")}</h2>
      <p>{w("pack.check.lead")}</p>
      <div className="av-field">
        <label className="av-field__label" htmlFor={id}>{w("pack.check.label")}</label>
        <textarea id={id} className="av-textarea av-trial-short" value={text} onChange={(e) => setText(e.target.value)} />
      </div>
      <div className="av-trial-actions">
        <Button variant="secondary" onClick={run} disabled={!text.trim()}>{w("pack.check.run")}</Button>
      </div>
      {message && <Notice tone="error"><p>{message.text}</p></Notice>}
      {result && (
        <ol className="av-trial-sentences">
          {result.sentences.map((s) => (
            <li key={s.index} className={s.sourced ? "av-trial-sourced" : "av-trial-unsourced"}>
              <p className="av-trial-prose">{s.text}</p>
              <p className="av-trial-muted">
                {s.source?.kind === "report"
                  ? w("pack.check.from_report", { para: s.source.para_no })
                  : s.source?.kind === "written_answer"
                    ? w("pack.check.from_answer", { question: s.source.question_no })
                    : w("pack.check.not_served")}
              </p>
              {!s.sourced && s.note_id && s.choice === "tell" && <p className="av-trial-strong">{w("pack.check.told")}</p>}
              {!s.sourced && s.note_id && s.choice === "leave_out" && <p className="av-trial-strong">{w("pack.check.left_out")}</p>}
              {!s.sourced && s.note_id && !s.choice && frame.can("report.build") && (
                <div className="av-trial-actions">
                  <Button variant="secondary" onClick={() => choose(s.note_id!, "tell")}>{w("pack.check.tell", { party: partyWord(frame) })}</Button>
                  <Button variant="secondary" onClick={() => choose(s.note_id!, "leave_out")}>{w("pack.check.leave_out")}</Button>
                </div>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function OfflineCopy({ frame }: { frame: Frame }) {
  const id = useId();
  const path = { case_id: frame.caseId };
  const stepUp = useStepUp();
  const list = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/offline-packages", { params: { path } })), [frame.caseId]);
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
