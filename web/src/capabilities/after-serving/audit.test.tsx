// Audit fixes for S19, S20, S21, S21b and S29: the case's own party word, the server's word on a stale trial pack,
// the shared back link, the experts' discussion link, "None." and one heading per dialog.
import { screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { AFTER_MESSAGES as M } from "./words";
import { casePack, claim, DOC, inCase, serve } from "./testing";
import type { Schemas } from "../../foundation/platform-client";

const movedTrial = { id: "h1", type: "trial", starts_on: "2027-06-14", ends_on: "2027-06-16", status: "scheduled", venue: "Court 4", direction_id: null, replaced_by_hearing_id: "h2", changed_at: null, version: 1 } as const;
const stale = (s: boolean) => ({ meetings: [], pack: { built_on: "2027-05-30", stale: s } });

test("S20 tells the party of this case, not the first party type in the pack", async () => {
  serve("launch");
  const pack = casePack("launch");
  const types = [...(pack.instructing_party_types as Array<{ key: string; label?: string }>), { key: "second_type", label: "second party" }];
  server.use(http.get("/v1/cases/:caseId/pack", () => json({ ...pack, instructing_party_types: types } as Schemas["CasePack"])));
  inCase("launch", { instructing_party_type: "second_type" });
  server.use(http.get("/v1/cases/:caseId/pack", () => json({ ...pack, instructing_party_types: types } as Schemas["CasePack"])));
  renderAt(`/cases/${CASE_A}/written-questions`);
  expect(await screen.findByRole("button", { name: "Tell the second party" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: `Tell the ${types[0]!.label}` })).toBeNull();
});

describe("S29 trial pack banner", () => {
  test("shown when the server says the pack is stale, with no moved hearing", async () => {
    serve("launch", { steps: stale(true) });
    renderAt(`/cases/${CASE_A}/court-orders`);
    expect(await screen.findByText(M["after_serving.orders.trial_moved"]!)).toBeInTheDocument();
  });
  test("hidden when the server says the pack is current, even with a moved hearing", async () => {
    serve("launch", { hearings: [movedTrial], steps: stale(false) });
    renderAt(`/cases/${CASE_A}/court-orders`);
    expect(await screen.findByText("Court 4 · 3 days")).toBeInTheDocument();
    expect(screen.queryByText(M["after_serving.orders.trial_moved"]!)).toBeNull();
  });
});

describe.each(["written-questions", "new-material", "correct-report", "court-orders"])("back link on %s", (route) => {
  test("is the shared back link with its arrow", async () => {
    serve("launch");
    const { container } = renderAt(`/cases/${CASE_A}/${route}`);
    const link = await screen.findByRole("link", { name: M["after_serving.back"]! });
    expect(link).toHaveClass("av-back-link");
    expect(container.querySelector("a[aria-hidden='true']")).toBeNull();
  });
});

test("S21 offers the experts' discussion even when no positions differ", async () => {
  serve("launch", {
    opposing: { id: "r1", document_id: DOC, received_on: "2026-12-09", checked_at: "2026-12-09T10:00:00Z", claims_found: 1, claims: [claim()], positions: [] },
  });
  renderAt(`/cases/${CASE_A}/new-material?document=${DOC}`);
  expect(await screen.findByText("Haemoglobin was 89 in February")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: new RegExp(M["after_serving.new.prepare"]!) })).toBeInTheDocument();
});

test("S19 changes card starts with None. when nothing has changed", async () => {
  serve("launch", { changes: [] });
  renderAt(`/cases/${CASE_A}/served`);
  expect(await screen.findByText(/^None\. If you spot an error/)).toBeInTheDocument();
});

test("S29 withdraw dialog shows its title once", async () => {
  serve("launch", { directions: [{ id: "x", made_on: "2027-01-20", reference: "ref-1", text: "Order.", stay: true, document_id: null, created_deadline_ids: [], created_at: "2027-01-20T10:00:00Z" }] });
  const { user } = renderAt(`/cases/${CASE_A}/court-orders`);
  await user.click(await screen.findByRole("button", { name: M["after_serving.hold.withdraw"]! }));
  const dialog = await screen.findByRole("dialog", { name: M["after_serving.hold.withdraw"] });
  expect(within(dialog).getAllByRole("heading", { name: M["after_serving.hold.withdraw"]! })).toHaveLength(1);
});
