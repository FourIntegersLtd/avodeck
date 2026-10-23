// The 412 choice for any form that saves with If-Match: another window saved first, so show what the
// person typed and what was saved, and let them keep theirs or take the saved one. Typed work is never
// thrown away: closing the box chooses neither.
import type { ReactNode } from "react";
import { t } from "../i18n";
import { Button } from "./Button";
import { Dialog } from "./Dialog";

export interface StaleChoiceWords {
  title?: string;
  keepMine?: string;
  useSaved?: string;
}

export interface StaleChoiceProps<T> {
  /** What the person typed here. */
  draft: T;
  /** The server's latest saved value (the 412's `saved`), or null if it sent none. */
  saved: T | null;
  /** The saved value's version, for the next If-Match; 0 when unknown. */
  savedVersion?: number | null;
  /** Keep the draft: save it again against `savedVersion`. */
  onKeepMine: (savedVersion: number) => void;
  /** Take the saved value instead of the draft. */
  onUseSaved: (saved: T | null, savedVersion: number) => void;
  /** Closed without choosing: the draft stays as typed. */
  onClose?: () => void;
  /** How to show a value; plain text by default. */
  show?: (value: T) => ReactNode;
  /** A screen's own words; generic words otherwise. */
  words?: StaleChoiceWords;
}

export function StaleChoice<T>({ draft, saved, savedVersion, onKeepMine, onUseSaved, onClose, show, words = {} }: StaleChoiceProps<T>) {
  const view = show ?? ((v: T) => String(v));
  const version = savedVersion ?? 0;
  return (
    <Dialog title={words.title ?? t("stale.title")} onClose={onClose ?? (() => undefined)}>
      <div className="av-stack">
        <div className="av-stale-value">{view(draft)}</div>
        <Button onClick={() => onKeepMine(version)}>{words.keepMine ?? t("stale.keep_mine")}</Button>
        {saved !== null && <div className="av-stale-value">{view(saved)}</div>}
        <Button variant="secondary" onClick={() => onUseSaved(saved, version)}>
          {words.useSaved ?? t("stale.use_saved")}
        </Button>
      </div>
    </Dialog>
  );
}
