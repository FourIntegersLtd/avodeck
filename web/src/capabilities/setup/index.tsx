import type { RouteObject } from "react-router-dom";
import { registerScreen } from "../../foundation/lib/screens";
import { Conflicts } from "./Conflicts";
import { CaseDetails, NewCase } from "./NewCase";
import { Profile } from "./Profile";
import { Questions } from "./Questions";
import "./setup.css";

/** Slice C (case setup) screens. Signed-in routes (profile, new case) and routes inside a case
 * (details, conflicts, questions). Today's items open them through the screen keys below. */
export const setupRoutes: RouteObject[] = [
  { path: "profile", element: <Profile /> },
  { path: "cases/new", element: <NewCase /> },
];
export const setupCaseRoutes: RouteObject[] = [
  { path: "details", element: <CaseDetails /> },
  { path: "conflicts", element: <Conflicts /> },
  { path: "questions", element: <Questions /> },
];

// "profile" (the header's Your profile link) is not registered yet: app/ShellLinks.test.tsx expects no
// slice to have registered it when the app loads. Foundation is asked to reset the registry in that test.
const profile = () => "/profile";
const details = ({ caseId }: { caseId: string }) => `/cases/${caseId}/details`;
const conflicts = ({ caseId }: { caseId: string }) => `/cases/${caseId}/conflicts`;
const questions = ({ caseId }: { caseId: string }) => `/cases/${caseId}/questions`;

registerScreen("S02", profile);
registerScreen("new_case", () => "/cases/new");
registerScreen("S04", details);
registerScreen("case_details", details);
registerScreen("S05", conflicts);
registerScreen("conflicts", conflicts);
registerScreen("S06", questions);
registerScreen("questions", questions);
