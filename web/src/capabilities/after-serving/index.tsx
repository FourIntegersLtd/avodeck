import type { RouteObject } from "react-router-dom";
import { registerScreen } from "../../foundation/lib/screens";
import { AddendumScreen } from "./Addendum";
import { CourtOrders } from "./CourtOrders";
import { NewMaterial } from "./NewMaterial";
import { QuestionsScreen } from "./Questions";
import { ServedScreen } from "./Served";
import "./after-serving.css";
import "./words";

/** Slice G (after serving) screens, inside a case: S19 After serving, S20 Questions from the other side, S21 New
 * records and the other expert's report, S21b Correct or update your report and S29 Court orders and hearings.
 * Today's items open them through the screen keys below. */
export const afterServingCaseRoutes: RouteObject[] = [
  { path: "served", element: <ServedScreen />, handle: { screen: "after_serving" } },
  { path: "written-questions", element: <QuestionsScreen />, handle: { screen: "written_questions" } },
  { path: "new-material", element: <NewMaterial />, handle: { screen: "new_material" } },
  { path: "correct-report", element: <AddendumScreen />, handle: { screen: "correct_report" } },
  { path: "court-orders", element: <CourtOrders />, handle: { screen: "after_serving" } },
];

const at = (segment: string) => ({ caseId }: { caseId: string }) => `/cases/${caseId}/${segment}`;

for (const key of ["S19", "after_serving"]) registerScreen(key, at("served"));
for (const key of ["S20", "written_questions"]) registerScreen(key, at("written-questions"));
for (const key of ["S21", "new_material"]) registerScreen(key, at("new-material"));
for (const key of ["S21b", "correct_report"]) registerScreen(key, at("correct-report"));
for (const key of ["S29", "court_orders"]) registerScreen(key, at("court-orders"));
