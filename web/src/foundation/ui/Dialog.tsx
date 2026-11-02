import * as RadixDialog from "@radix-ui/react-dialog";
import { useState, type ReactNode } from "react";

export interface DialogProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** An extra class on the box, for a wider or animated look. */
  className?: string;
}

/**
 * A modal box on the Radix dialog primitive: focus moves in and is trapped, Escape closes it,
 * the page behind is hidden from assistive technology and cannot scroll, and focus returns afterwards.
 * A click on the backdrop does not close it (as before).
 */
export function Dialog({ title, onClose, children, className }: DialogProps) {
  // Radix returns focus only to its own trigger; callers mount this on demand, so remember who had focus.
  const [before] = useState(() => document.activeElement as HTMLElement | null);
  return (
    <RadixDialog.Root open onOpenChange={(open) => !open && onClose()}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="av-dialog-backdrop">
          <RadixDialog.Content
            aria-modal="true"
            onCloseAutoFocus={(e) => {
              e.preventDefault();
              before?.focus();
            }}
            className={className ? `av-dialog ${className}` : "av-dialog"}
            aria-describedby={undefined}
            onInteractOutside={(e) => e.preventDefault()}
          >
            <RadixDialog.Title asChild>
              <h2>{title}</h2>
            </RadixDialog.Title>
            {children}
          </RadixDialog.Content>
        </RadixDialog.Overlay>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
