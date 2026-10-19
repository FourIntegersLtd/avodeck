import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { t } from "../i18n";
import { Logo } from "./Logo";

export interface FocusedHeaderProps {
  /** The task's name beside the logo, e.g. "New case". */
  title: ReactNode;
  /** Where Cancel goes. Leave it out for no Cancel. */
  cancelTo?: string;
  /** Other words for Cancel. */
  cancelLabel?: ReactNode;
}

/**
 * The header of a screen that is one task (S04 board): logo | the task's name on the left, Cancel on
 * the right, and no menu, bell or avatar. The screen renders it itself; AppShell draws no header there.
 */
export function FocusedHeader({ title, cancelTo, cancelLabel }: FocusedHeaderProps) {
  return (
    <header className="av-focused-bar">
      <Logo />
      <span className="av-case-bar__divider" aria-hidden="true" />
      <span className="av-focused-bar__title">{title}</span>
      {cancelTo && (
        <Link to={cancelTo} className="av-focused-bar__cancel">
          {cancelLabel ?? t("ui.cancel")}
        </Link>
      )}
    </header>
  );
}
