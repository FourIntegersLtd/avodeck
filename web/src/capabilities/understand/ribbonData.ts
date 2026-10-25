// The ribbon's data: the whole record counted per lane per month, plus pins for the entries worth seeing from
// far away (things to look at and question links). One shape (the API's Overview) whichever request filled it.
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";

export type RibbonData = Schemas["Overview"];
/** The overview for a case, as the record stood on `day` (or as it stands now), counted only from `from` to `to` when given. */
export function useRibbonData(caseId: string, day: string, from = "", to = "", enabled = true) {
  return useResource(
    async () => {
      if (!enabled) return null;
      const query: { as_seen_on?: string; from?: string; to?: string } = {};
      if (day) query.as_seen_on = day;
      if (from) query.from = from;
      if (to) query.to = to;
      return unwrap(api.GET("/v1/cases/{case_id}/timeline/overview", { params: { path: { case_id: caseId }, query } }));
    },
    [caseId, day, from, to, enabled],
  );
}
