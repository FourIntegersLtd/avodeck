// S29 Court orders and hearings: the dates in the expert's timezone, a court order (with the stay box), a hearing,
// moving a date (If-Match, and the keep-which-version prompt on 412).
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { launchWords } from "../setup/testing";
import { AFTER_MESSAGES as M } from "./words";
import { D1, deadline, firstKind, serve, type PackRun } from "./testing";

const url = `/cases/${CASE_A}/court-orders`;

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("lists the dates with the kind in words and the day in the case's timezone, and passes axe", async () => {
    serve(run, { deadlines: [deadline({ kind: firstKind(run), due_on: "2026-12-26", source: "direction" })] });
    const { container } = renderAt(url);
    expect(await screen.findByRole("heading", { level: 2, name: M["after_serving.orders.title"] })).toBeInTheDocument();
    expect(await screen.findByText("Due 26 December 2026")).toBeInTheDocument();
    expect(screen.getByText(M["after_serving.source.direction"]!)).toBeInTheDocument();
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("moving a date sends the new day and reason with the version it saw", async () => {
  const { sent, record } = serve("launch", { deadlines: [deadline({ kind: "trial", version: 3 })] });
  server.use(http.patch("/v1/cases/:caseId/deadlines/:id", async ({ request }) => (await record(request), json(deadline({ kind: "trial", due_on: "2027-01-09", version: 4 })))));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: M["after_serving.deadlines.move"]! }));
  const dialog = await screen.findByRole("dialog", { name: M["after_serving.deadlines.move_title"] });
  await user.type(within(dialog).getByLabelText(M["after_serving.deadlines.new_date"]!), "2027-01-09");
  await user.selectOptions(within(dialog).getByLabelText(M["after_serving.deadlines.reason"]!), "court_order");
  await user.click(within(dialog).getByRole("button", { name: M["after_serving.deadlines.move_submit"]! }));
  await waitFor(() => expect(sent).toContainEqual({ method: "PATCH", path: `/v1/cases/${CASE_A}/deadlines/${D1}`, body: { due_on: "2027-01-09", reason: "court_order" }, ifMatch: '"3"' }));
});

test("when someone else moved the date first, it asks which version to keep and then sends the new version", async () => {
  let version = 1;
  const { sent, record } = serve("launch");
  server.use(
    http.get("/v1/cases/:caseId/deadlines", () => json({ deadlines: [deadline({ version })], served_on: "2026-11-28" })),
    http.patch("/v1/cases/:caseId/deadlines/:id", async ({ request }) => {
      await record(request);
      if (sent.length === 1) {
        version = 2;
        return apiError(412, "stale");
      }
      return json(deadline({ version: 3 }));
    }),
  );
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: M["after_serving.deadlines.move"]! }));
  const dialog = await screen.findByRole("dialog", { name: M["after_serving.deadlines.move_title"] });
  await user.type(within(dialog).getByLabelText(M["after_serving.deadlines.new_date"]!), "2027-01-09");
  await user.click(within(dialog).getByRole("button", { name: M["after_serving.deadlines.move_submit"]! }));
  const stale = await screen.findByRole("dialog", { name: M["after_serving.stale_title"] });
  await user.click(within(stale).getByRole("button", { name: M["after_serving.stale_mine"]! }));
  await waitFor(() => expect(sent).toHaveLength(2));
  expect(sent[1]).toMatchObject({ ifMatch: '"2"', body: { due_on: "2027-01-09" } });
});

test("a court order is saved with its date, reference, the date it sets and the stay box", async () => {
  const { sent, record } = serve("launch");
  server.use(http.post("/v1/cases/:caseId/court-directions", async ({ request }) => (await record(request), json({ id: "x", made_on: "2027-01-20", reference: "ref-1", text: "The order.", stay: true, document_id: null, created_deadline_ids: [], created_at: "2027-01-20T10:00:00Z" }, 201))));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: M["after_serving.orders.add"]! }));
  await user.type(screen.getByLabelText(M["after_serving.orders.made_on"]!), "2027-01-20");
  await user.type(screen.getByLabelText(M["after_serving.orders.reference"]!), "ref-1");
  await user.type(screen.getByLabelText(M["after_serving.orders.text"]!), "The order.");
  await user.selectOptions(screen.getByLabelText(M["after_serving.orders.deadline_kind"]!), "trial");
  await user.type(screen.getByLabelText(M["after_serving.orders.deadline_due"]!), "2027-02-12");
  await user.click(screen.getByRole("checkbox", { name: M["after_serving.orders.stay"]! }));
  await user.click(screen.getByRole("button", { name: M["after_serving.orders.add_submit"]! }));
  await waitFor(() => expect(sent).toHaveLength(1));
  expect(sent[0]).toMatchObject({ method: "POST", body: { made_on: "2027-01-20", reference: "ref-1", text: "The order.", stay: true, deadlines: [{ kind: "trial", due_on: "2027-02-12" }] } });
});

test("a hearing is saved with its kind, days and place; hearings and orders already recorded are listed", async () => {
  const { sent, record } = serve("launch", {
    hearings: [{ id: "h1", type: "trial", starts_on: "2027-06-14", ends_on: "2027-06-16", status: "scheduled", venue: "Court 4", direction_id: null, replaced_by_hearing_id: null, changed_at: null, version: 1 }],
    directions: [{ id: "x", made_on: "2027-01-20", reference: "ref-1", text: "The experts shall discuss.", stay: false, document_id: null, created_deadline_ids: [], created_at: "2027-01-20T10:00:00Z" }],
  });
  server.use(http.post("/v1/cases/:caseId/hearings", async ({ request }) => (await record(request), json({ id: "h2", type: "other", starts_on: "2027-07-01", ends_on: "2027-07-02", status: "scheduled", venue: "Room 2", direction_id: null, replaced_by_hearing_id: null, changed_at: null, version: 1 }, 201))));
  const { user } = renderAt(url);
  expect(await screen.findByText("Court 4")).toBeInTheDocument();
  expect(screen.getByText(M["after_serving.hearings.status.scheduled"]!)).toBeInTheDocument();
  expect(screen.getByText("The experts shall discuss.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: M["after_serving.hearings.add"]! }));
  await user.selectOptions(screen.getByLabelText(M["after_serving.hearings.type"]!), "other");
  await user.type(screen.getByLabelText(M["after_serving.hearings.starts_on"]!), "2027-07-01");
  await user.type(screen.getByLabelText(M["after_serving.hearings.ends_on"]!), "2027-07-02");
  await user.type(screen.getByLabelText(M["after_serving.hearings.venue"]!), "Room 2");
  await user.click(screen.getByRole("button", { name: M["after_serving.hearings.add_submit"]! }));
  await waitFor(() => expect(sent).toHaveLength(1));
  expect(sent[0]).toMatchObject({ method: "POST", body: { type: "other", starts_on: "2027-07-01", ends_on: "2027-07-02", venue: "Room 2" } });
});

test("the history of a moved date shows what it was and what it is now", async () => {
  serve("launch", { deadlineChanges: { [D1]: [{ id: "c", at: "2027-01-20T10:00:00Z", changed_by: null, old_due_on: "2026-12-26", new_due_on: "2027-01-09", reason: "court_order", source: "direction", source_id: null }] } });
  renderAt(url);
  expect(await screen.findByText(M["after_serving.deadlines.history"]!)).toBeInTheDocument();
  expect(await screen.findByText("Was 26 December 2026")).toBeInTheDocument();
  expect(screen.getByText("Now 9 January 2027")).toBeInTheDocument();
});
