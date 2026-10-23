import type { RouteObject } from "react-router-dom";
import { registerScreen } from "../../foundation/lib/screens";
import { AcceptInvite } from "./AcceptInvite";
import { CloseAccount, Custodian } from "./AccountSections";
import { CaseTeam } from "./CaseTeam";
import { Leave } from "./Leave";
import { OrganisationSettings } from "./OrganisationSettings";
import { SupportConsole } from "./SupportConsole";
import "./words";

/** Slice O: S27 and its Leave page, S28's custodian and closing sections, and the support console. */
registerScreen("organisation_settings", ({ params }) => `/organisations/${String(params.organisation_id)}`);
registerScreen("S27", ({ params }) => `/organisations/${String(params.organisation_id)}`);
registerScreen("organisation_leave", ({ params }) => `/organisations/${String(params.organisation_id)}/leave`);
registerScreen("account_custodian", () => "/account/custodian");
registerScreen("account_close", () => "/account/close");
registerScreen("case_team", ({ caseId }) => `/cases/${caseId}/team`);
registerScreen("support_console", () => "/support");

export const organisationsRoutes: RouteObject[] = [
  { path: "organisations/:orgId", element: <OrganisationSettings /> },
  { path: "organisations/:orgId/leave", element: <Leave /> },
  { path: "account/custodian", element: <Custodian /> },
  { path: "account/close", element: <CloseAccount /> },
  { path: "support", element: <SupportConsole /> },
];

export const organisationsCaseRoutes: RouteObject[] = [{ path: "team", element: <CaseTeam /> }];

/** The invite link works for someone who is not signed in yet, so it mounts outside the signed-in guard. */
export const organisationsOfflineRoutes: RouteObject[] = [{ path: "invites/:token", element: <AcceptInvite /> }];
