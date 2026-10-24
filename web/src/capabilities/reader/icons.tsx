// Small 20px line icons for the reader toolbar, drawn in the current text colour (the foundation has no
// icon set yet; a shared one can replace this file later).
import type { ReactNode } from "react";

function Icon({ children, size = 20 }: { children: ReactNode; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  );
}

export const TurnIcon = () => (
  <Icon>
    <path d="M21 12a9 9 0 1 1-3-6.7" />
    <path d="M21 3v6h-6" />
  </Icon>
);
export const FindIcon = () => (
  <Icon>
    <circle cx="11" cy="11" r="6" />
    <path d="M20 20l-4.5-4.5" />
  </Icon>
);
export const ViewIcon = () => (
  <Icon>
    <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" />
    <circle cx="12" cy="12" r="3" />
  </Icon>
);
export const FullScreenIcon = ({ leave }: { leave?: boolean }) => (
  <Icon>
    <path d={leave ? "M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" : "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"} />
  </Icon>
);
export const ChevronIcon = ({ dir }: { dir: "left" | "right" | "down" | "up" }) => (
  <Icon>
    <path d={{ left: "M15 6l-6 6 6 6", right: "M9 6l6 6-6 6", down: "M6 9l6 6 6-6", up: "M6 15l6-6 6 6" }[dir]} />
  </Icon>
);
export const TickIcon = () => (
  <Icon>
    <path d="M5 12.5l4.5 4.5L19 7" />
  </Icon>
);
export const SlidersIcon = () => (
  <Icon>
    <path d="M4 7h10M18 7h2M4 17h2M10 17h10" />
    <circle cx="16" cy="7" r="2" />
    <circle cx="8" cy="17" r="2" />
  </Icon>
);
