// Line drawings the pack can name for a lane (S11) or a check (S13). The pack gives a plain word (`icon`); this
// file only holds the generic drawings. A word with no drawing here falls back to the label's first letter.
import type { ReactNode } from "react";

const P = (d: string) => <path d={d} />;

export const LINE_ICONS: Record<string, ReactNode> = {
  visit: P("M12 4v16M4 12h16"),
  flask: P("M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3"),
  moon: P("M20 14A8 8 0 1 1 10 4a6 6 0 0 0 10 10z"),
  pill: P("M8 8l8 8M10.5 5.5a4 4 0 0 1 5.7 5.7l-5 5a4 4 0 0 1-5.7-5.7z"),
  mail: P("M4 6h16v12H4zM4 7l8 6 8-6"),
  calendar: P("M5 5h14v15H5zM5 10h14M9 3v4M15 3v4"),
};

/** The drawing for an icon word, or null when this screen has none. */
export function hasIcon(icon: string | null | undefined): icon is string {
  return !!icon && Object.prototype.hasOwnProperty.call(LINE_ICONS, icon);
}

export function LineIcon({ icon, size = 20 }: { icon: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {LINE_ICONS[icon]}
    </svg>
  );
}

export function LaneIcon({ icon, label }: { icon: string | null | undefined; label: string }) {
  return hasIcon(icon) ? <LineIcon icon={icon} /> : <>{label.slice(0, 1).toUpperCase()}</>;
}

/** The "check the date" mark: a small amber calendar with a question mark. The words come from the caller. */
export function DateMarkIcon() {
  return (
    <svg className="av-story-datemark" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d="M4 5h16v15H4zM4 10h16M8 3v4M16 3v4M10 13.5a2 2 0 1 1 2.5 1.9c-.4.2-.5.5-.5 1M12 18.2v.1" />
    </svg>
  );
}
