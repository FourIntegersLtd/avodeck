import type { RouteObject } from "react-router-dom";
import { registerScreen } from "../../foundation/lib/screens";
import { registerAccountSection, registerCaseAction } from "../../foundation/lib/slots";
import { AcceptInvite } from "./AcceptInvite";
import { CloseAccount, CloseAccountBody, Custodian, CustodianBody } from "./AccountSections";
import { CaseTeam } from "./CaseTeam";
import { flag } from "./flags";
import { Leave } from "./Leave";
import { OrganisationSettings } from "./OrganisationSettings";
import { StartTeam, StartTeamBody } from "./StartTeam";
import { SupportCase } from "./SupportCase";
import { SupportAccounts } from "./SupportAccounts";
import { SupportConsole } from "./SupportConsole";
import "./words";

/** Slice O: S27 and its Leave page, S28's custodian and closing sections, and the support console. */
registerScreen("organisation_settings", ({ params }) => `/organisations/${String(params.organisation_id)}`);
registerScreen("S27", ({ params }) => `/organisations/${String(params.organisation_id)}`);
registerScreen("organisation_new", () => "/organisations/new");
registerScreen("organisation_leave", ({ params }) => `/organisations/${String(params.organisation_id)}/leave`);
registerScreen("account_custodian", () => "/account/custodian");
registerScreen("account_close", () => "/account/close");
registerScreen("case_team", ({ caseId }) => `/cases/${caseId}/team`);
registerScreen("support_console", () => "/support");

registerAccountSection({ key: "organisations.start_team", order: 5, titleKey: "organisations.start_team.title", component: StartTeamBody });
registerAccountSection({ key: "organisations.custodian", order: 10, titleKey: "organisations.custodian.title", component: CustodianBody });
registerAccountSection({ key: "organisations.close", order: 20, titleKey: "organisations.close_account.title", component: CloseAccountBody });

const toTeam = ({ caseId }: { caseId: string }) => `/cases/${caseId}/team`;
// One entry for the case's settings page: shown when any of its cards (named expert, replacement case, support) is.
registerCaseAction({
  key: "organisations.case_settings",
  order: 10,
  labelKey: "organisations.case_settings",
  to: toTeam,
  visible: (c) =>
    (!c.served && c.allowedActions.includes("case.reassign_named_expert")) ||
    c.allowedActions.includes("support_access.grant") ||
    (c.served && flag("replacement_case") && c.allowedActions.includes("case.reassign_named_expert")),
});

export const organisationsRoutes: RouteObject[] = [
  // Before ":orgId", or the settings screen would swallow "new".
  { path: "organisations/new", element: <StartTeam /> },
  { path: "organisations/:orgId", element: <OrganisationSettings /> },
  { path: "organisations/:orgId/leave", element: <Leave /> },
  { path: "account/custodian", element: <Custodian /> },
  { path: "account/close", element: <CloseAccount /> },
  { path: "support", element: <SupportConsole /> },
  { path: "support/cases/:caseId", element: <SupportCase /> },
  { path: "support/accounts", element: <SupportAccounts /> },
];

export const organisationsCaseRoutes: RouteObject[] = [{ path: "team", element: <CaseTeam /> }];

/** The invite link works for someone who is not signed in yet, so it mounts outside the signed-in guard. */
export const organisationsOfflineRoutes: RouteObject[] = [{ path: "invites/:token", element: <AcceptInvite /> }];
