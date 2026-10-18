import type { RouteObject } from "react-router-dom";
import { registerScreen } from "../../foundation/lib/screens";
import { FinalCheck } from "./FinalCheck";
import { InvoiceScreen } from "./Invoice";
import { ReportScreen } from "./Report";
import { SignSend } from "./SignSend";
import "./report.css";
import "./words";

/** Slice F (report and sign) screens, inside a case: S16 Your report, S17 Final check, S18 Sign and send and
 * S18b Time and invoice. Today's items open them through the screen keys below. */
export const reportCaseRoutes: RouteObject[] = [
  { path: "report", element: <ReportScreen />, handle: { screen: "S16" } },
  { path: "final-check", element: <FinalCheck />, handle: { screen: "S17" } },
  { path: "sign", element: <SignSend />, handle: { screen: "S18" } },
  { path: "invoice", element: <InvoiceScreen />, handle: { screen: "S18b" } },
];

const at = (segment: string) => ({ caseId }: { caseId: string }) => `/cases/${caseId}/${segment}`;

for (const key of ["S16", "report"]) registerScreen(key, at("report"));
for (const key of ["S17", "final_check"]) registerScreen(key, at("final-check"));
for (const key of ["S18", "sign", "sign_send"]) registerScreen(key, at("sign"));
for (const key of ["S18b", "invoice"]) registerScreen(key, at("invoice"));
