// S24 Trial pack (docs/design/screens/S24-TrialPack.dc.html): choose what goes in, make the pack from the
// served report on the server, download it behind the code, check a new note against what was served, and
// put a sealed read-only copy on this device for the witness box (made, put there and removed behind the code).
import { useEffect, useId, useRef, useState } from "react";
import { humanise, t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, LinkButton, LoadError, Loading, Notice, TextField } from "../../foundation/ui";
import { longDate, saveBlob, tag, vocab } from "./data";
import { TrialFrame, TrialHeader, type Frame } from "./frame";
import { forgetCopy, hasCopy, saveCopy } from "./offline";
import { w, type TrialKey } from "./words";

type Section = Schemas["TrialPackSettingsIn"]["sections"][number];
type Message = { tone: "success" | "error" | "info"; text: string } | null;

const SCREEN = "trial_pack";
const POLL_MS = 800;
const SECTIONS: Section[] = ["report", "chronology", "joint_statement", "written_answers", "legal_tests"];
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
  return (
    <div className="av-page av-stack">
      <TrialHeader pack={frame.pack} screen={SCREEN} title={w("pack.title")} />
      <p>{w("pack.lead")}</p>
      <Contents frame={frame} />
      <Check frame={frame} />
      {frame.can("report.sign") && <OfflineCopy frame={frame} />}
    </div>
  );
}

function sectionLabel(section: Section, frame: Frame): string {
  if (section === "joint_statement") return w("pack.section.joint_statement", { document: vocab(frame.pack, "expert_meeting_document") });
  return w(`pack.section.${section}` as TrialKey);
}

function Contents({ frame }: { frame: Frame }) {
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

  const pack = latest.data.pack;
  const canBuild = frame.can("report.build");
  return (
    <>
      {message && <Notice tone={message.tone}><p>{message.text}</p></Notice>}
      <fieldset className="av-card av-stack">
        <legend className="av-trial-legend">{w("pack.sections.heading")}</legend>
        {SECTIONS.map((s) => (
          <Choice key={s} label={sectionLabel(s, frame)} hint={w(`pack.section.${s}_hint` as TrialKey)} checked={chosen.has(s)} onChange={() => toggle(s)} disabled={!canBuild} />
        ))}
        <Choice label={w("pack.section.guidance")} hint={w("pack.section.guidance_hint")} checked={guidance ?? false} onChange={() => setGuidance((g) => !g)} disabled={!canBuild} />
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
            <p className="av-trial-muted">{w("pack.large_print")}</p>
          </>
        )}
        <div className="av-trial-actions">
          {pack && frame.can("case.view_content") && <Button variant="secondary" onClick={download}>{w("pack.download")}</Button>}
          {canBuild && <Button onClick={build} disabled={job !== null}>{pack ? w("pack.rebuild") : w("pack.build")}</Button>}
        </div>
      </section>
    </>
  );
}

function Choice({ label, hint, checked, onChange, disabled }: { label: string; hint: string; checked: boolean; onChange: () => void; disabled?: boolean }) {
  const id = useId();
  return (
    <div className="av-trial-choice-row">
      <label className="av-checkbox">
        <input type="checkbox" checked={checked} onChange={onChange} disabled={disabled} aria-describedby={id} />
        <span>{label}</span>
      </label>
      <p id={id} className="av-trial-muted av-trial-hint">{hint}</p>
    </div>
  );
}

function Check({ frame }: { frame: Frame }) {
  const id = useId();
  const [text, setText] = useState("");
  const [result, setResult] = useState<Schemas["NoteChecked"] | null>(null);
  const [message, setMessage] = useState<Message>(null);
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
      await saveCopy(sealed, { label: copy.device_label, locale: frame.locale, expiresAt: copy.expires_at, kinds });
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
            <div className="av-trial-actions">
              <Button variant="danger" onClick={() => remove(copy)}>{w("pack.offline.delete")}</Button>
              {copy.status === "built" && <Button onClick={() => download(copy)}>{w("pack.offline.download")}</Button>}
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
