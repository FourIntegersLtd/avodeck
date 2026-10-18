import type { RouteObject } from "react-router-dom";
import { registerScreen } from "../../foundation/lib/screens";
import { Opinion } from "./Opinion";
import { Points } from "./Points";
import "./write.css";
import "./words";

/** Slice E (write) screens, inside a case: S14 Points against and S15 Your opinion. The question is chosen
 * with ?q=<questionId>. Today's items open them through the screen keys below. */
export const writeCaseRoutes: RouteObject[] = [
  { path: "points", element: <Points />, handle: { screen: "S14" } },
  { path: "opinion", element: <Opinion />, handle: { screen: "S15" } },
];

const points = ({ caseId }: { caseId: string }) => `/cases/${caseId}/points`;
const opinion = ({ caseId }: { caseId: string }) => `/cases/${caseId}/opinion`;

registerScreen("S14", points);
registerScreen("points", points);
registerScreen("S15", opinion);
registerScreen("opinion", opinion);
