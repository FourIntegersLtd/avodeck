// Slice A (bundle pipeline): screens 07, 08, 08b and 30, inside a case.
import type { RouteObject } from "react-router-dom";
import { registerScreen } from "../../foundation/lib/screens";
import { AskInstructingParty } from "./AskInstructingParty";
import { BundleReady } from "./BundleReady";
import { Copies } from "./Copies";
import { NotThisPatient } from "./NotThisPatient";
import { Preparing } from "./Preparing";
import "./pipeline.css";
import "./words";

registerScreen("preparing", ({ caseId }) => `/cases/${caseId}/preparing`);
registerScreen("bundle_ready", ({ caseId }) => `/cases/${caseId}/bundle`);
registerScreen("bundle_copies", ({ caseId }) => `/cases/${caseId}/bundle/copies`);
registerScreen("ask_instructing_party", ({ caseId }) => `/cases/${caseId}/bundle/ask`);
registerScreen("not_this_patient", ({ caseId }) => `/cases/${caseId}/bundle/not-this-patient`);

export const pipelineCaseRoutes: RouteObject[] = [
  { path: "preparing", element: <Preparing />, handle: { screen: "preparing" } },
  { path: "bundle", element: <BundleReady />, handle: { screen: "bundle_ready" } },
  { path: "bundle/copies", element: <Copies />, handle: { screen: "bundle_ready" } },
  { path: "bundle/ask", element: <AskInstructingParty />, handle: { screen: "ask_instructing_party" } },
  { path: "bundle/not-this-patient", element: <NotThisPatient />, handle: { screen: "bundle_ready" } },
];
