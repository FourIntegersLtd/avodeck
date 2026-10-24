import type { ReactNode } from "react";

/** A grey capsule holding ‹ value › (page, zoom): put quiet buttons and the value inside. */
export function StepperCapsule({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div role="group" aria-label={label} className="av-capsule">
      {children}
    </div>
  );
}
