import type { RouteObject } from "react-router-dom";
import { registerScreen } from "../../foundation/lib/screens";
import { Conflicts } from "./Conflicts";
import { CaseDetails, NewCase } from "./NewCase";
import { Profile } from "./Profile";
import { Questions } from "./Questions";
import "./setup.css";
import "./profile.css";
import "./newcase.css";
import "./conflicts.css";
import "./questions.css";
import "./words";

/** Slice C (case setup) screens. Signed-in routes (profile, new case) and routes inside a case
 * (details, conflicts, questions). Today's items open them through the screen keys below. */
export const setupRoutes: RouteObject[] = [
  { path: "profile", element: <Profile /> },
  { path: "cases/new", element: <NewCase /> },
];
export const setupCaseRoutes: RouteObject[] = [
  { path: "details", element: <CaseDetails />, handle: { screen: "new_case" } },
  { path: "conflicts", element: <Conflicts />, handle: { screen: "conflicts" } },
  { path: "questions", element: <Questions />, handle: { screen: "questions" } },
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
