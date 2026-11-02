import type { RouteObject } from "react-router-dom";
import { lazyScreen } from "../../foundation/lib/lazyScreen";
import { registerScreen } from "../../foundation/lib/screens";
import "./report.css";
import "./words";

/** Slice F (report and sign) screens, inside a case: S16 Your report, S17 Final check, S18 Sign and send and
 * S18b Time and invoice. Today's items open them through the screen keys below. */
export const reportCaseRoutes: RouteObject[] = [
  { path: "report", ...lazyScreen(() => import("./Report"), ({ ReportScreen }) => <ReportScreen />), handle: { screen: "report" } },
  { path: "final-check", ...lazyScreen(() => import("./FinalCheck"), ({ FinalCheck }) => <FinalCheck />), handle: { screen: "final_check" } },
  { path: "sign", ...lazyScreen(() => import("./SignSend"), ({ SignSend }) => <SignSend />), handle: { screen: "sign_send" } },
  { path: "invoice", ...lazyScreen(() => import("./Invoice"), ({ InvoiceScreen }) => <InvoiceScreen />), handle: { screen: "invoice" } },
];

/** The colleague's spot-check page: opened from an emailed link, with no sign-in (threat model T9). */
export const reportOfflineRoutes: RouteObject[] = [{ path: "spot-check/:token", ...lazyScreen(() => import("./SpotCheck"), ({ SpotCheckScreen }) => <SpotCheckScreen />) }];

const at = (segment: string) => ({ caseId }: { caseId: string }) => `/cases/${caseId}/${segment}`;

for (const key of ["S16", "report"]) registerScreen(key, at("report"));
for (const key of ["S17", "final_check"]) registerScreen(key, at("final-check"));
for (const key of ["S18", "sign", "sign_send"]) registerScreen(key, at("sign"));
for (const key of ["S18b", "invoice"]) registerScreen(key, at("invoice"));
