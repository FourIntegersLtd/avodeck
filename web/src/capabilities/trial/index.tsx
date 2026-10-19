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

/** S22-S24: inside the case frame, which draws the case bar and marks the pack step that holds each screen. */
export const trialCaseRoutes: RouteObject[] = [
  { path: "joint-statement", element: <JointStatement />, handle: { screen: "expert_meeting" } },
  { path: "trial-pages", element: <TrialPages />, handle: { screen: "court_bundle_pages" } },
  { path: "trial-pack", element: <TrialPack />, handle: { screen: "trial_pack" } },
];

/** S25: works from the copy on this device with no connection and no sign-in (rule 4's exception). */
export const trialOfflineRoutes: RouteObject[] = [{ path: "cases/:caseId/witness-box", element: <Lookup /> }];
