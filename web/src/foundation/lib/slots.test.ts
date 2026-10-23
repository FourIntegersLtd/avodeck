import { afterEach, expect, test } from "vitest";
import { accountSections, caseActions, registerAccountSection, registerCaseAction, resetSlots, type CaseActionContext } from "./slots";

afterEach(() => resetSlots());

const ctx: CaseActionContext = {
  caseId: "c1",
  status: "active",
  served: false,
  onHold: false,
  allowedActions: ["case.view_content"],
  isNamedExpert: true,
  orgRole: "owner",
};

test("account sections come back in order, whatever order they registered in", () => {
  registerAccountSection({ key: "b", order: 20, titleKey: "account.notify.title", component: () => null });
  registerAccountSection({ key: "a", order: 10, titleKey: "account.devices.title", component: () => null });
  expect(accountSections().map((s) => s.key)).toEqual(["a", "b"]);
});

test("registering a section twice under one key replaces it", () => {
  registerAccountSection({ key: "a", order: 10, titleKey: "account.devices.title", component: () => null });
  registerAccountSection({ key: "a", order: 5, titleKey: "account.notify.title", component: () => null });
  expect(accountSections()).toHaveLength(1);
  expect(accountSections()[0]!.order).toBe(5);
});

test("case actions are filtered by their predicate and ordered", () => {
  registerCaseAction({ key: "z", order: 2, labelKey: "app.case.back", to: ({ caseId }) => `/cases/${caseId}/z`, visible: () => true });
  registerCaseAction({ key: "a", order: 1, labelKey: "app.case.back", to: ({ caseId }) => `/cases/${caseId}/a`, visible: (c) => !c.served });
  registerCaseAction({ key: "h", order: 3, labelKey: "app.case.back", to: () => "/h", visible: (c) => c.served });
  expect(caseActions(ctx).map((a) => [a.key, a.to])).toEqual([
    ["a", "/cases/c1/a"],
    ["z", "/cases/c1/z"],
  ]);
  expect(caseActions({ ...ctx, served: true }).map((a) => a.key)).toEqual(["z", "h"]);
});

test("an action with no predicate is always shown", () => {
  registerCaseAction({ key: "x", order: 1, labelKey: "app.case.back", to: () => "/x" });
  expect(caseActions(ctx)).toHaveLength(1);
});
