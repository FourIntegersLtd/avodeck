import type { RouteObject } from "react-router-dom";
import { lazyScreen } from "../../foundation/lib/lazyScreen";
import { registerScreen } from "../../foundation/lib/screens";
import { registerAccountSection, registerCaseAction } from "../../foundation/lib/slots";
import { CloseAccountBody, CustodianBody } from "./AccountSections";
import { flag } from "./flags";
import { StartTeamBody } from "./StartTeam";
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
  { path: "organisations/new", ...lazyScreen(() => import("./StartTeam"), ({ StartTeam }) => <StartTeam />) },
  { path: "organisations/:orgId", ...lazyScreen(() => import("./OrganisationSettings"), ({ OrganisationSettings }) => <OrganisationSettings />) },
  { path: "organisations/:orgId/leave", ...lazyScreen(() => import("./Leave"), ({ Leave }) => <Leave />) },
  { path: "account/custodian", ...lazyScreen(() => import("./AccountSections"), ({ Custodian }) => <Custodian />) },
  { path: "account/close", ...lazyScreen(() => import("./AccountSections"), ({ CloseAccount }) => <CloseAccount />) },
  { path: "support", ...lazyScreen(() => import("./SupportConsole"), ({ SupportConsole }) => <SupportConsole />) },
  { path: "support/cases/:caseId", ...lazyScreen(() => import("./SupportCase"), ({ SupportCase }) => <SupportCase />) },
  { path: "support/accounts", ...lazyScreen(() => import("./SupportAccounts"), ({ SupportAccounts }) => <SupportAccounts />) },
];

export const organisationsCaseRoutes: RouteObject[] = [{ path: "team", ...lazyScreen(() => import("./CaseTeam"), ({ CaseTeam }) => <CaseTeam />) }];

/** The invite link works for someone who is not signed in yet, so it mounts outside the signed-in guard. */
export const organisationsOfflineRoutes: RouteObject[] = [{ path: "invites/:token", ...lazyScreen(() => import("./AcceptInvite"), ({ AcceptInvite }) => <AcceptInvite />) }];
