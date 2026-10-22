// The ribbon's data: the whole record counted per lane per month, plus pins for the entries worth seeing from
// far away (things to look at and question links). One shape (the API's Overview) whichever request filled it.
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { axisIso } from "./shared";
import { monthlyCounts } from "./storyLayout";

export type RibbonData = Schemas["Overview"];
type Timeline = Pick<Schemas["Timeline"], "lanes" | "unplaced" | "range">;

/** The overview worked out from a whole-record timeline reply. */
export function ribbonFromTimeline(body: Timeline): RibbonData {
  const range = body.range ?? null;
  const counts = range ? monthlyCounts(body.lanes, range.first, range.last) : null;
  const pins = body.lanes
    .flatMap((lane) =>
      lane.events
        .filter((e) => axisIso(e) && (e.has_finding || e.question_ids.length > 0))
        .map((e) => ({ fact_id: e.fact_id, date: axisIso(e)!, lane_key: lane.key, has_finding: e.has_finding, question_ids: e.question_ids })),
    )
    .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  return {
    range,
    months: counts?.months ?? [],
    lanes: body.lanes.map((lane) => ({ key: lane.key, label: lane.label, colour: lane.colour ?? null, counts: counts?.byLane[lane.key] ?? [] })),
    pins,
  };
}

/** The ribbon for a case, as the record stood on `day` (or as it stands now). */
export function useRibbonData(caseId: string, day: string) {
  return useResource(
    () =>
      unwrap(
        api.GET("/v1/cases/{case_id}/timeline/overview", {
          params: { path: { case_id: caseId }, query: day ? { as_seen_on: day } : {} },
        }),
      ),
    [caseId, day],
  );
}
