import type { Schemas } from "../../foundation/platform-client";
import { axisIso } from "./shared";

type Event = Schemas["Event"];
type Lane = Schemas["Timeline"]["lanes"][number];

export type ChronologyEntry = Event & { laneKey: string; laneLabel: string };

const dayMs = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const [y, m, d] = iso.split("-").map(Number);
  return Date.UTC(y!, (m ?? 1) - 1, d ?? 1);
};

/** Flatten lanes and unplaced into one date-ordered list (undated last, then by page). */
export function chronologyEntries(lanes: Lane[], unplaced: Event[]): ChronologyEntry[] {
  const rows: ChronologyEntry[] = [];
  for (const lane of lanes) {
    for (const e of lane.events) {
      rows.push({ ...e, laneKey: lane.key, laneLabel: lane.label });
    }
  }
  for (const e of unplaced) {
    rows.push({ ...e, laneKey: "", laneLabel: "" });
  }
  rows.sort((a, b) => {
    const da = dayMs(axisIso(a));
    const db = dayMs(axisIso(b));
    if (da === null && db === null) return a.page_no - b.page_no;
    if (da === null) return 1;
    if (db === null) return -1;
    if (da !== db) return da - db;
    return a.page_no - b.page_no;
  });
  return rows;
}
