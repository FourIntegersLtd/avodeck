import type { RouteObject } from "react-router-dom";
import { registerScreen } from "../../foundation/lib/screens";
import { JointStatement } from "./JointStatement";
import { Lookup } from "./Lookup";
import { TrialPack } from "./TrialPack";
import { TrialPages } from "./TrialPages";
import "./words";

for (const [screen, path] of [["S22", "joint-statement"], ["S23", "trial-pages"], ["S24", "trial-pack"], ["S25", "witness-box"]] as const) {
  registerScreen(screen, ({ caseId }) => `/cases/${caseId}/${path}`);
}
registerScreen("expert_meeting", ({ caseId }) => `/cases/${caseId}/joint-statement`);
registerScreen("court_bundle_pages", ({ caseId }) => `/cases/${caseId}/trial-pages`);
registerScreen("trial_pack", ({ caseId }) => `/cases/${caseId}/trial-pack`);
registerScreen("witness_box", ({ caseId }) => `/cases/${caseId}/witness-box`);

/** S22-S24: signed-in, each with its own case header. */
export const trialRoutes: RouteObject[] = [
  { path: "cases/:caseId/joint-statement", element: <JointStatement /> },
  { path: "cases/:caseId/trial-pages", element: <TrialPages /> },
  { path: "cases/:caseId/trial-pack", element: <TrialPack /> },
];

/** S25: works from the copy on this device with no connection and no sign-in (rule 4's exception). */
export const trialOfflineRoutes: RouteObject[] = [{ path: "cases/:caseId/witness-box", element: <Lookup /> }];
