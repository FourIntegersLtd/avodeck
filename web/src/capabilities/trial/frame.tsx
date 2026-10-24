// What every signed-in trial screen shares. The case frame (app/CaseLayout) draws the case bar and loads
// the case and its pack once; a trial screen reads them from it and draws its own title under the bar.
import type { ReactNode } from "react";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { useSession } from "../../foundation/lib/session";
import type { Schemas } from "../../foundation/platform-client";
import { Spinner, useBusy } from "../../foundation/ui";
import { w } from "./words";
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
  const { item, pack } = useCaseFrame();
  const { me } = useSession();
  const actions = new Set(item.allowed_actions);
  return <>{children({ caseId: item.id, item, pack, can: (a) => actions.has(a), locale: me.person.locale || pack.locale })}</>;
}

/** A screen's page under the case bar: a width (the boards differ) and the spacing between its parts. */
export function TrialPage({ width, children }: { width: "s22" | "s23" | "s24"; children: ReactNode }) {
  return <div className={`av-page av-stack av-trial-page av-trial-page--${width}`}>{children}</div>;
}

/** The screen's title (the case name in the bar is the page's h1), with its lead under it. */
export function TrialTitle({ title, children }: { title: ReactNode; children?: ReactNode }) {
  return (
    <header className="av-trial-title">
      <h2>{title}</h2>
      {children}
    </header>
  );
}

/** Choosing a PDF: the label looks like a button and is the input's name. While the file is being sent it shows
 * a spinner and "Sending…", and takes no second file. */
export function PdfPicker({ label, onFile, disabled, link }: { label: string; onFile: (file: File) => unknown; disabled?: boolean; link?: boolean }) {
  const { busy, run } = useBusy();
  const off = disabled || busy;
  return (
    <label className={`${link ? "av-text-link" : "av-button av-button--secondary"} av-trial-picker${off ? " av-trial-picker--disabled" : ""}`}>
      {busy && <Spinner />}
      {label}
      {busy && <span role="status"> {w("picker.sending")}</span>}
      <input
        type="file"
        accept="application/pdf"
        className="av-trial-picker__input"
        disabled={off}
        aria-busy={busy ? "true" : undefined}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) void run(async () => { await onFile(file); });
        }}
      />
    </label>
  );
}
