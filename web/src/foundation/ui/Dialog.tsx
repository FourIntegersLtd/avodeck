import { useEffect, useId, useRef, type ReactNode } from "react";

export interface DialogProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
}

/** A modal box: focus moves into it, Escape closes it, and focus returns afterwards. */
export function Dialog({ title, onClose, children }: DialogProps) {
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
        className="av-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
        }}
      >
        <h2 id={titleId}>{title}</h2>
        {children}
      </div>
    </div>
  );
}
