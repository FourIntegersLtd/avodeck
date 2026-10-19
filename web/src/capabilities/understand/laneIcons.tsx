// S11 lane icons. The pack has no icon field, so the launch lane keys map to simple line drawings here;
// any other key (for example in the fixture packs) falls back to the first letter of its label.
import type { ReactNode } from "react";

const P = (d: string) => <path d={d} />;

export const LANE_ICONS: Record<string, ReactNode> = {
  primary_care_visits: P("M12 4v16M4 12h16"),
  results: P("M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3"),
  out_of_hours: P("M20 14A8 8 0 1 1 10 4a6 6 0 0 0 10 10z"),
  prescriptions: P("M8 8l8 8M10.5 5.5a4 4 0 0 1 5.7 5.7l-5 5a4 4 0 0 1-5.7-5.7z"),
  hospital_and_letters: P("M4 6h16v12H4zM4 7l8 6 8-6"),
  appointments: P("M5 5h14v15H5zM5 10h14M9 3v4M15 3v4"),
};

export function LaneIcon({ laneKey, label }: { laneKey: string; label: string }) {
  const icon = Object.prototype.hasOwnProperty.call(LANE_ICONS, laneKey) ? LANE_ICONS[laneKey] : null;
  if (!icon) return <>{label.slice(0, 1).toUpperCase()}</>;
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {icon}
    </svg>
  );
}
