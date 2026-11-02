import type { RouteObject } from "react-router-dom";
import { lazyScreen } from "../../foundation/lib/lazyScreen";
import { registerScreen } from "../../foundation/lib/screens";
import "./setup.css";
import "./words";

/** Slice C (case setup) screens. Signed-in routes (profile, new case) and routes inside a case
 * (details, conflicts, questions). Today's items open them through the screen keys below. */
export const setupRoutes: RouteObject[] = [
  { path: "profile", ...lazyScreen(() => import("./Profile"), ({ Profile }) => <Profile />) },
  { path: "cases/new", ...lazyScreen(() => import("./NewCase"), ({ NewCase }) => <NewCase />) },
];
export const setupCaseRoutes: RouteObject[] = [
  { path: "details", ...lazyScreen(() => import("./NewCase"), ({ CaseDetails }) => <CaseDetails />), handle: { screen: "new_case" } },
  { path: "conflicts", ...lazyScreen(() => import("./Conflicts"), ({ Conflicts }) => <Conflicts />), handle: { screen: "conflicts" } },
  { path: "questions", ...lazyScreen(() => import("./Questions"), ({ Questions }) => <Questions />), handle: { screen: "questions" } },
];

const profile = () => "/profile";
const details = ({ caseId }: { caseId: string }) => `/cases/${caseId}/details`;
const conflicts = ({ caseId }: { caseId: string }) => `/cases/${caseId}/conflicts`;
const questions = ({ caseId }: { caseId: string }) => `/cases/${caseId}/questions`;

registerScreen("S02", profile);
registerScreen("profile", profile);
registerScreen("new_case", () => "/cases/new");
registerScreen("S04", details);
registerScreen("case_details", details);
registerScreen("S05", conflicts);
registerScreen("conflicts", conflicts);
registerScreen("S06", questions);
registerScreen("questions", questions);
