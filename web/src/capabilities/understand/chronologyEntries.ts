import type { Schemas } from "../../foundation/platform-client";

type Event = Schemas["Event"];
type Timeline = Pick<Schemas["Timeline"], "lanes" | "unplaced" | "chronology">;

export type ChronologyEntry = Event & { laneKey: string; laneLabel: string; laneIcon?: string | null };

/** The list view's rows: the server's chronology (its order, its lane words) joined to the entries it names. */
export function chronologyEntries(data: Timeline): ChronologyEntry[] {
  const byId = new Map<string, Event>();
  for (const lane of data.lanes) for (const e of lane.events) byId.set(e.fact_id, e);
  for (const e of data.unplaced) byId.set(e.fact_id, e);
  return data.chronology.flatMap((ref) => {
    const e = byId.get(ref.fact_id);
    return e ? [{ ...e, laneKey: ref.lane_key, laneLabel: ref.lane_label, laneIcon: ref.lane_icon }] : [];
  });
}
