// S29 as designed: the rebuild-trial-pack banner, hearings with status, orders with the dates they set, the table of
// how dates changed, and the hold / withdraw block.
import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, test } from "vitest";
import { registerScreen } from "../../foundation/lib/screens";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import type { Schemas } from "../../foundation/platform-client";
import { D1, deadline, serve } from "./testing";

const url = `/cases/${CASE_A}/court-orders`;
const hearing = (over: Partial<Schemas["HearingOut"]>): Schemas["HearingOut"] => ({
  id: "h1", type: "trial", starts_on: "2027-06-14", ends_on: "2027-06-16", status: "scheduled", venue: "Court 4", direction_id: null, replaced_by_hearing_id: null, changed_at: null, version: 1, ...over,
});
const moved = [hearing({ id: "h0", starts_on: "2027-03-03", ends_on: "2027-03-05", status: "adjourned", replaced_by_hearing_id: "h1" }), hearing({})];

beforeEach(() => registerScreen("trial_pack", ({ caseId }) => `/cases/${caseId}/trial-pack`));

test("when the trial moved there is a banner with the way to rebuild the trial pack", async () => {
  serve("launch", { hearings: moved });
  const { container } = renderAt(url);
  const banner = await screen.findByRole("alert");
  expect(within(banner).getByText("Your trial pack was made for the old trial date.")).toBeInTheDocument();
  expect(within(banner).getByRole("link", { name: "Rebuild trial pack" })).toHaveAttribute("href", `/cases/${CASE_A}/trial-pack`);
  expect(await axeViolations(container)).toEqual([]);
});

test("with no moved trial there is no banner", async () => {
  serve("launch", { hearings: [hearing({})] });
  renderAt(url);
  await screen.findByText("Court 4 · 3 days");
  expect(screen.queryByRole("alert")).toBeNull();
});

test("a hearing row shows its days, kind, place and state, with Change on a listed one only", async () => {
  serve("launch", { hearings: moved });
  renderAt(url);
  const rows = await screen.findAllByRole("listitem", { name: /Trial/ });
  expect(within(rows[0]!).getByText("14–16 Jun 2027")).toBeInTheDocument();
  expect(within(rows[0]!).getByText("Court 4 · 3 days")).toBeInTheDocument();
  expect(within(rows[0]!).getByText("Listed")).toBeInTheDocument();
  expect(within(rows[0]!).getByRole("button", { name: "Change" })).toBeInTheDocument();
  expect(within(rows[1]!).getByText("Adjourned")).toBeInTheDocument();
  expect(within(rows[1]!).queryByRole("button", { name: "Change" })).toBeNull();
});

test("an order shows its words and the dates it set", async () => {
  serve("launch", {
    deadlines: [deadline({ id: D1, kind: "trial", due_on: "2027-02-12", source: "direction" })],
    directions: [{ id: "x", made_on: "2027-01-20", reference: "Claim 12", text: "The experts shall discuss.", stay: false, document_id: null, created_deadline_ids: [D1], created_at: "2027-01-20T10:00:00Z" }],
  });
  renderAt(url);
  const order = await screen.findByRole("article", { name: "Order of 20 January 2027" });
  expect(within(order).getByText("Claim 12")).toBeInTheDocument();
  expect(within(order).getByText("The experts shall discuss.")).toBeInTheDocument();
  expect(within(order).getByText("Dates it set: Trial, 12 Feb")).toBeInTheDocument();
});

test("how dates changed is a table with the old date struck through", async () => {
  serve("launch", {
    deadlines: [deadline({ id: D1, kind: "trial", due_on: "2027-01-09" })],
    deadlineChanges: { [D1]: [{ id: "c", at: "2027-01-20T10:00:00Z", changed_by: null, old_due_on: "2026-12-26", new_due_on: "2027-01-09", reason: "court_order", source: "direction", source_id: null }] },
  });
  renderAt(url);
  const table = await screen.findByRole("table", { name: "How your dates have changed" });
  for (const head of ["Deadline", "Was", "Now", "Why"]) expect(within(table).getByRole("columnheader", { name: head })).toBeInTheDocument();
  expect(within(table).getByText("26 Dec")).toHaveStyle({ textDecoration: "line-through" });
  expect(within(table).getByText("9 Jan")).toBeInTheDocument();
});

test("the case can be put on hold, and taken off again", async () => {
  const { sent, record } = serve("launch");
  server.use(http.post("/v1/cases/:caseId/hold", async ({ request }) => (await record(request), json({ on_hold: true }))));
  const { user } = renderAt(url);
  expect(await screen.findByText("Has the case been paused, or are you stepping away?")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Put on hold" }));
  await waitFor(() => expect(sent[0]).toMatchObject({ method: "POST", path: `/v1/cases/${CASE_A}/hold` }));
});

test("withdrawing asks for a reason, then the code, and says the report stays locked", async () => {
  let stepped = false;
  const bodies: unknown[] = [];
  serve("launch");
  server.use(
    http.post("/v1/cases/:caseId/withdraw", async ({ request }) => {
      if (!stepped) return apiError(403, "step_up_required");
      bodies.push(await request.json());
      return json({ status: "withdrawn", deadlines_cancelled: 2, note: { key: "k", params: {}, text: "t" } } as never);
    }),
    http.post("/v1/auth/mfa/verify", () => ((stepped = true), json({ status: "signed_in", recovery_codes: null }))),
  );
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: "Withdraw from the case" }));
  const dialog = await screen.findByRole("dialog", { name: "Withdraw from the case" });
  const go = within(dialog).getByRole("button", { name: "Withdraw" });
  expect(go).toBeDisabled();
  await user.type(within(dialog).getByLabelText("Why are you withdrawing?"), "Conflict found.");
  await user.click(go);
  const code = await screen.findByRole("dialog", { name: "Please enter your code" });
  await user.type(within(code).getByLabelText("6-digit code"), "123456");
  await user.click(within(code).getByRole("button", { name: "Carry on" }));
  expect(await screen.findByText("You have withdrawn. Your served report stays locked.")).toBeInTheDocument();
  expect(bodies).toEqual([{ reason: "Conflict found." }]);
});
