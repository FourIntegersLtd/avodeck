// S29: every action shows it is working and ignores a second click while it runs.
import { screen, within } from "@testing-library/react";
import { test } from "vitest";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { AFTER_MESSAGES as M } from "./words";
import { byName, expectBusyOnce, hold } from "./busyTesting";
import { deadline, serve } from "./testing";

const url = `/cases/${CASE_A}/court-orders`;
const hearing = { id: "h1", type: "trial", starts_on: "2027-06-14", ends_on: "2027-06-16", status: "scheduled", venue: "Court 4", direction_id: null, replaced_by_hearing_id: null, changed_at: null, version: 1 };

test("putting the case on hold goes busy and sends once", async () => {
  serve("launch");
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/hold", async () => (await h.enter(), json({} as never))));
  const { user } = renderAt(url);
  try {
    await expectBusyOnce(user, await byName(M["after_serving.hold.put"]!), h);
  } finally { h.release(); }
});

test("withdrawing goes busy and sends once", async () => {
  serve("launch");
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/withdraw", async () => (await h.enter(), json({} as never))));
  const { user } = renderAt(url);
  try {
    await user.click(await byName(M["after_serving.hold.withdraw"]!));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(M["after_serving.hold.reason"]!), "Settled");
    await expectBusyOnce(user, within(dialog).getByRole("button", { name: M["after_serving.hold.confirm"]! }), h);
  } finally { h.release(); }
});

test("moving a date goes busy and sends once", async () => {
  serve("launch", { deadlines: [deadline({ kind: "trial" })] });
  const h = hold();
  server.use(http.patch("/v1/cases/:caseId/deadlines/:id", async () => (await h.enter(), json(deadline({ version: 2 })))));
  const { user } = renderAt(url);
  try {
    await user.click(await byName(M["after_serving.deadlines.move"]!));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(M["after_serving.deadlines.new_date"]!), "2027-01-09");
    await expectBusyOnce(user, within(dialog).getByRole("button", { name: M["after_serving.deadlines.move_submit"]! }), h);
  } finally { h.release(); }
});

test("keeping my version of a moved date goes busy and sends once", async () => {
  serve("launch");
  const h = hold();
  let first = true;
  server.use(http.patch("/v1/cases/:caseId/deadlines/:id", async () => {
    if (first) { first = false; return apiError(412, "stale"); }
    await h.enter();
    return json(deadline({ version: 3 }));
  }));
  const { user } = renderAt(url);
  try {
    await user.click(await byName(M["after_serving.deadlines.move"]!));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(M["after_serving.deadlines.new_date"]!), "2027-01-09");
    await user.click(within(dialog).getByRole("button", { name: M["after_serving.deadlines.move_submit"]! }));
    const stale = await screen.findByRole("dialog", { name: M["after_serving.stale_title"] });
    await expectBusyOnce(user, within(stale).getByRole("button", { name: M["after_serving.stale_mine"]! }), h);
  } finally { h.release(); }
});

test("saving a court order goes busy and sends once", async () => {
  serve("launch");
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/court-directions", async () => (await h.enter(), json({ id: "x", made_on: "2027-01-20", stay: false, document_id: null, created_deadline_ids: [], created_at: "2027-01-20T10:00:00Z" } as never, 201))));
  const { user } = renderAt(url);
  try {
    await user.click(await byName(M["after_serving.orders.add"]!));
    await user.type(screen.getByLabelText(M["after_serving.orders.made_on"]!), "2027-01-20");
    await expectBusyOnce(user, screen.getByRole("button", { name: M["after_serving.orders.add_submit"]! }), h);
  } finally { h.release(); }
});

test("saving a hearing goes busy and sends once", async () => {
  serve("launch");
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/hearings", async () => (await h.enter(), json(hearing as never, 201))));
  const { user } = renderAt(url);
  try {
    await user.click(await byName(M["after_serving.hearings.add"]!));
    await user.type(screen.getByLabelText(M["after_serving.hearings.starts_on"]!), "2027-07-01");
    await user.type(screen.getByLabelText(M["after_serving.hearings.ends_on"]!), "2027-07-02");
    await expectBusyOnce(user, screen.getByRole("button", { name: M["after_serving.hearings.add_submit"]! }), h);
  } finally { h.release(); }
});

test("adjourning a hearing goes busy and sends once", async () => {
  serve("launch", { hearings: [hearing as never] });
  const h = hold();
  server.use(http.patch("/v1/cases/:caseId/hearings/:id", async () => (await h.enter(), json({ ...hearing, status: "adjourned", version: 2 } as never))));
  const { user } = renderAt(url);
  try {
    await user.click(await byName(M["after_serving.hearings.change"]!));
    await expectBusyOnce(user, await byName(M["after_serving.hearings.adjourn"]!), h);
  } finally { h.release(); }
});
