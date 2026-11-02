// Slice A (bundle pipeline): screens 07, 08, 08b and 30, inside a case.
import type { RouteObject } from "react-router-dom";
import { lazyScreen } from "../../foundation/lib/lazyScreen";
import { registerScreen } from "../../foundation/lib/screens";
import "./pipeline.css";
import "./words";

registerScreen("case_home", ({ caseId }) => `/cases/${caseId}/home`);
registerScreen("add_papers", ({ caseId }) => `/cases/${caseId}/papers/add`);
registerScreen("preparing", ({ caseId }) => `/cases/${caseId}/preparing`);
registerScreen("bundle_ready", ({ caseId }) => `/cases/${caseId}/bundle`);
registerScreen("bundle_copies", ({ caseId }) => `/cases/${caseId}/bundle/copies`);
registerScreen("ask_instructing_party", ({ caseId }) => `/cases/${caseId}/bundle/ask`);
registerScreen("not_this_patient", ({ caseId }) => `/cases/${caseId}/bundle/not-this-patient`);

export const pipelineCaseRoutes: RouteObject[] = [
  { path: "home", ...lazyScreen(() => import("./CaseHome"), ({ CaseHome }) => <CaseHome />) },
  { path: "papers/add", ...lazyScreen(() => import("./AddPapers"), ({ AddPapers }) => <AddPapers />) },
  { path: "preparing", ...lazyScreen(() => import("./Preparing"), ({ Preparing }) => <Preparing />), handle: { screen: "preparing" } },
  { path: "bundle", ...lazyScreen(() => import("./BundleReady"), ({ BundleReady }) => <BundleReady />), handle: { screen: "bundle_ready" } },
  { path: "bundle/copies", ...lazyScreen(() => import("./Copies"), ({ Copies }) => <Copies />), handle: { screen: "bundle_ready" } },
  { path: "bundle/ask", ...lazyScreen(() => import("./AskInstructingParty"), ({ AskInstructingParty }) => <AskInstructingParty />), handle: { screen: "ask_instructing_party" } },
  { path: "bundle/not-this-patient", ...lazyScreen(() => import("./NotThisPatient"), ({ NotThisPatient }) => <NotThisPatient />), handle: { screen: "bundle_ready" } },
];
