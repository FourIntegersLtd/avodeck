// The frame every signed-in trial screen shares: it loads the case and its pack, then shows the case's
// step pills with the screen's own title as the page heading.
import type { ReactNode } from "react";
import { useParams } from "react-router-dom";
import { useSession } from "../../foundation/lib/session";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { CaseHeader, LoadError, Loading } from "../../foundation/ui";
import "./trial.css";

export interface Frame {
  caseId: string;
  item: Schemas["Case"];
  pack: Schemas["CasePack"];
  can: (action: string) => boolean;
  /** The person's locale for dates, else the pack's. */
  locale: string;
}

export function TrialFrame({ children }: { children: (frame: Frame) => ReactNode }) {
  const { caseId = "" } = useParams();
  const { me } = useSession();
  const path = { case_id: caseId };
  const item = useResource(() => unwrap(api.GET("/v1/cases/{case_id}", { params: { path } })), [caseId]);
  const pack = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/pack", { params: { path } })), [caseId]);
  const error = item.error ?? pack.error;
  if (error) return <div className="av-page"><LoadError onRetry={() => { item.reload(); pack.reload(); }} /></div>;
  if (!item.data || !pack.data) return <div className="av-page"><Loading /></div>;
  const actions = new Set(item.data.allowed_actions);
  return <>{children({ caseId, item: item.data, pack: pack.data, can: (a) => actions.has(a), locale: me.person.locale || pack.data.locale })}</>;
}

/** The case's step pills with the screen's title as the page heading. */
export function TrialHeader({ pack, title, screen }: { pack: Schemas["CasePack"]; title: string; screen: string }) {
  const steps = pack.workflow_steps.map((s) => ({ key: String(s.key) }));
  const current = pack.workflow_steps.find((s) => Array.isArray(s.screens) && (s.screens as unknown[]).includes(screen))?.key;
  return <CaseHeader steps={steps} current={current === undefined ? null : String(current)} title={<h1>{title}</h1>} />;
}

/** Choosing a PDF: the label looks like a button and is the input's name. */
export function PdfPicker({ label, onFile, disabled, link }: { label: string; onFile: (file: File) => void; disabled?: boolean; link?: boolean }) {
  return (
    <label className={`${link ? "av-text-link" : "av-button av-button--secondary"} av-trial-picker${disabled ? " av-trial-picker--disabled" : ""}`}>
      {label}
      <input
        type="file"
        accept="application/pdf"
        className="av-trial-picker__input"
        disabled={disabled}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) onFile(file);
        }}
      />
    </label>
  );
}
