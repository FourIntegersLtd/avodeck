// S11 lane icons. The pack has no icon field, so the launch lane keys map to simple line drawings here;
// any other key (for example in the fixture packs) falls back to the first letter of its label.
import type { ReactNode } from "react";

const P = (d: string) => <path d={d} />;

export const LANE_ICONS: Record<string, ReactNode> = {
  primary_care_visits: (
    <>
      {P("M6 3v6a4 4 0 008 0V3")}
      {P("M10 13v3a5 5 0 0010 0v-2")}
      <circle cx="20" cy="12" r="2" />
    </>
  ),
  results: P("M9 3h6M10 3v6L5 18a2 2 0 002 3h10a2 2 0 002-3l-5-9V3"),
  out_of_hours: P("M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"),
  prescriptions: (
    <>
      <rect x="3" y="9" width="18" height="7" rx="3.5" transform="rotate(-35 12 12.5)" />
      {P("M9.5 8.5l5 7")}
    </>
  ),
  hospital_and_letters: (
    <>
      {P("M4 21V5a2 2 0 012-2h12a2 2 0 012 2v16")}
      {P("M9 21v-5h6v5M12 7v6M9 10h6")}
    </>
  ),
  appointments: (
    <>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      {P("M3 10h18M8 3v4M16 3v4")}
    </>
  ),
};

export function LaneIcon({ laneKey, label }: { laneKey: string; label: string }) {
  const icon = Object.prototype.hasOwnProperty.call(LANE_ICONS, laneKey) ? LANE_ICONS[laneKey] : null;
  if (!icon) return <>{label.slice(0, 1).toUpperCase()}</>;
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {icon}
    </svg>
  );
}
