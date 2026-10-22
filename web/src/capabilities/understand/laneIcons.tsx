// Line drawings the pack can name for a lane (S11) or a check (S13). The pack gives a plain word (`icon`); this
// file only holds the generic drawings. A word with no drawing here falls back to the label's first letter.
import type { ReactNode } from "react";

const P = (d: string) => <path d={d} />;

export const LINE_ICONS: Record<string, ReactNode> = {
  visit: (
    <>
      {P("M6 3v6a4 4 0 008 0V3")}
      {P("M10 13v3a5 5 0 0010 0v-2")}
      <circle cx="20" cy="12" r="2" />
    </>
  ),
  flask: P("M9 3h6M10 3v6L5 18a2 2 0 002 3h10a2 2 0 002-3l-5-9V3"),
  moon: P("M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"),
  pill: (
    <>
      <rect x="3" y="9" width="18" height="7" rx="3.5" transform="rotate(-35 12 12.5)" />
      {P("M9.5 8.5l5 7")}
    </>
  ),
  mail: P("M4 6h16v12H4zM4 7l8 6 8-6"),
  calendar: (
    <>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      {P("M3 10h18M8 3v4M16 3v4")}
    </>
  ),
};

/** The drawing for an icon word, or null when this screen has none. */
export function hasIcon(icon: string | null | undefined): icon is string {
  return !!icon && Object.prototype.hasOwnProperty.call(LINE_ICONS, icon);
}

export function LineIcon({ icon, size = 24 }: { icon: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {LINE_ICONS[icon]}
    </svg>
  );
}

export function LaneIcon({ icon, label }: { icon: string | null | undefined; label: string }) {
  return hasIcon(icon) ? <LineIcon icon={icon} /> : <>{label.slice(0, 1).toUpperCase()}</>;
}
