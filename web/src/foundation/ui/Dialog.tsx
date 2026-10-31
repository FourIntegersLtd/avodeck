import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from "react";

export interface DialogProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** An extra class on the box, for a wider or animated look. */
  className?: string;
}

/** A modal box: focus moves into it, Escape closes it, and focus returns afterwards. */
export function Dialog({ title, onClose, children, className }: DialogProps) {
  const titleId = useId();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    const first = box.current?.querySelector<HTMLElement>("input, button, [href], textarea, select");
    (first ?? box.current)?.focus();
    return () => before?.focus();
  }, []);
  return (
    <div className="av-dialog-backdrop">
      <div
        ref={box}
        className={className ? `av-dialog ${className}` : "av-dialog"}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
          if (e.key === "Tab") keepFocusInside(e, box.current);
        }}
      >
        <h2 id={titleId}>{title}</h2>
        {children}
      </div>
    </div>
  );
}

const FOCUSABLE = "a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])";

/** Tab and Shift+Tab go round the box instead of out to the page behind it. */
function keepFocusInside(e: KeyboardEvent<HTMLElement>, box: HTMLElement | null) {
  const items = box ? [...box.querySelectorAll<HTMLElement>(FOCUSABLE)] : [];
  if (items.length === 0) return;
  const first = items[0]!;
  const last = items[items.length - 1]!;
  const active = document.activeElement;
  if (e.shiftKey && (active === first || active === box)) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && active === last) {
    e.preventDefault();
    first.focus();
  }
}
