// Slice O plugs into the foundation's slots: S28 sections on /account, case header actions, and the cases column.
import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { accountSections, caseActions, type CaseActionContext } from "../../foundation/lib/slots";
import { http, json, server, signedIn } from "../../test/api";
import { axeViolations, renderAt } from "../../test/render";
import { setFlag } from "./flags";
import "./index";
import { member, MEMBERS, PERSON_ID, TEAM_ORG, teamWorld } from "./testing";

vi.setConfig({ testTimeout: 30_000 });

const ctx = (over: Partial<CaseActionContext> = {}): CaseActionContext => ({
  caseId: "c1", status: "active", served: false, onHold: false, allowedActions: ["case.view_metadata", "case.reassign_named_expert"], isNamedExpert: false, orgRole: "admin", ...over,
});

test("the account page shows the custodian and closing sections through the slots", async () => {
  expect(accountSections().map((s) => s.key)).toEqual(expect.arrayContaining(["organisations.custodian", "organisations.close"]));
  signedIn();
  server.use(http.get("/v1/me/sessions", () => json([])), http.get("/v1/me/close", () => json({ cases: [] })));
  const { container } = renderAt("/account");
  expect(await screen.findByRole("heading", { name: "If you can't carry on" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Close your account" })).toBeInTheDocument();
  expect(screen.getByLabelText("Their name")).toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
});

// Reason: the three bar links became one "Case settings" action, visible when any of the three used to be.
const SETTINGS = "organisations.case_settings";

test("Case settings shows for owners and admins on an unserved case, and not otherwise", () => {
  expect(caseActions(ctx()).map((a) => a.labelKey)).toEqual([SETTINGS]);
  expect(caseActions(ctx())[0]!.to).toBe("/cases/c1/team");
  expect(caseActions(ctx({ allowedActions: ["case.view_metadata"] }))).toEqual([]);
  expect(caseActions(ctx({ served: true, status: "served" }))).toEqual([]);
});

test("the named expert gets Case settings for the support grant card", () => {
  expect(caseActions(ctx({ allowedActions: ["case.view_metadata", "support_access.grant"], orgRole: null, isNamedExpert: true })).map((a) => a.labelKey)).toEqual([SETTINGS]);
});

test("on a served case Case settings shows only behind the replacement flag, and only once", () => {
  const served = ctx({ served: true, status: "served" });
  expect(caseActions(served)).toEqual([]);
  setFlag("replacement_case", true);
  expect(caseActions(served).map((a) => a.labelKey)).toEqual([SETTINGS]);
  expect(caseActions({ ...served, allowedActions: ["case.reassign_named_expert", "support_access.grant"] })).toHaveLength(1);
  setFlag("replacement_case", false);
});

test("the people table has a cases column", async () => {
  teamWorld({ members: [{ ...MEMBERS[0]!, case_count: 1 }, { ...member(PERSON_ID.replace("1", "2"), "Lena Hart", "expert"), case_count: 6 }, { ...MEMBERS[1]!, case_count: 0 }] });
  renderAt(`/organisations/${TEAM_ORG}`);
  const people = await screen.findByRole("region", { name: /^People/ });
  expect(within(people).getByText("6 cases")).toBeInTheDocument();
  expect(within(people).getByText("1 case")).toBeInTheDocument();
  expect(within(people).getByText("—")).toBeInTheDocument();
});

test("a member who may not see counts gets no cases text at all", async () => {
  teamWorld({ members: MEMBERS.map((m) => ({ ...m, case_count: null })) });
  renderAt(`/organisations/${TEAM_ORG}`);
  const people = await screen.findByRole("region", { name: /^People/ });
  await within(people).findByText("Dr Jane Morgan (you)");
  expect(within(people).queryByText("—")).toBeNull();
  expect(within(people).queryByText(/^\d+ cases?$/)).toBeNull();
});
