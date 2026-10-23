// Slice D (understand): screens 11, 12 and 13, inside a case. Today's item opens S13.
import type { RouteObject } from "react-router-dom";
import { registerScreen } from "../../foundation/lib/screens";
import { Consultation } from "./Consultation";
import { ThingsToLook } from "./ThingsToLook";
import { PackScope } from "./shared";
import { Timeline } from "./Timeline";
import "./understand.css";
import "./words";

export const understandCaseRoutes: RouteObject[] = [
  { path: "timeline", element: <PackScope><Timeline /></PackScope>, handle: { screen: "timeline" } },
  { path: "consultation/:factId", element: <PackScope><Consultation /></PackScope>, handle: { screen: "as_clinician_saw_it" } },
  { path: "things-to-look-at", element: <PackScope><ThingsToLook /></PackScope>, handle: { screen: "things_to_look_at" } },
];

const timeline = ({ caseId }: { caseId: string }) => `/cases/${caseId}/timeline`;
const things = ({ caseId }: { caseId: string }) => `/cases/${caseId}/things-to-look-at`;

registerScreen("S11", timeline);
registerScreen("timeline", timeline);
registerScreen("S13", things);
registerScreen("things_to_look_at", things);
registerScreen("S12", ({ caseId, params }) => `/cases/${caseId}/consultation/${String(params.factId ?? params.fact_id ?? "")}`);
registerScreen("as_clinician_saw_it", ({ caseId, params }) => `/cases/${caseId}/consultation/${String(params.factId ?? params.fact_id ?? "")}`);
