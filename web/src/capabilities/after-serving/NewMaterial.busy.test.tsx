// S21: every action shows it is working and ignores a second click; choosing another report says it is updating.
import { screen, waitFor } from "@testing-library/react";
import { act } from "react";
import { expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { AFTER_MESSAGES as M } from "./words";
import { byName, expectBusyOnce, hold } from "./busyTesting";
import { claim, DOC, P1, Q1, serve } from "./testing";
import type { Schemas } from "../../foundation/platform-client";

const url = `/cases/${CASE_A}/new-material`;
const report = (claims: Schemas["OpposingClaimOut"][] = [claim()]): Schemas["OpposingReport"] => ({
  id: "r1", document_id: DOC, received_on: "2026-12-09", checked_at: "2026-12-09T10:00:00Z", claims_found: claims.length, claims,
  expert_name: null, expert_role: null, pages: null,
  positions: [{ question_id: Q1, question_no: 2, own: [{ paragraph_id: P1, para_no: 614, text: "Ours." }], theirs: [{ paragraph_ref: "5.1", text_verbatim: "Theirs." }] }],
});

test("checking the new pages goes busy and sends once", async () => {
  serve("launch", { pages: [{ page_id: "p1", page_no: 1, read: false, may_matter: [] }] });
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/new-since-served/check", async () => (await h.enter(), json({ job_id: "j" }, 202))));
  const { user } = renderAt(url);
  try {
    await expectBusyOnce(user, await byName(M["after_serving.new.check"]!), h);
  } finally { h.release(); }
});

test("registering their report goes busy and sends once", async () => {
  serve("launch", { opposing: null });
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/opposing-reports", async () => (await h.enter(), json({ id: "r1", job_id: "j" }, 202))));
  const { user } = renderAt(`${url}?document=${DOC}`);
  try {
    await user.type(await screen.findByLabelText(M["after_serving.new.received_on"]!), "2026-12-09");
    await expectBusyOnce(user, screen.getByRole("button", { name: M["after_serving.new.register"]! }), h);
  } finally { h.release(); }
});

test("confirming a difference goes busy and sends once", async () => {
  serve("launch", { opposing: report() });
  const h = hold();
  server.use(http.patch("/v1/cases/:caseId/opposing-claims/:id", async () => (await h.enter(), json(claim({ expert_confirmed: true, version: 2 })))));
  const { user } = renderAt(`${url}?document=${DOC}`);
  try {
    await expectBusyOnce(user, await byName(M["after_serving.new.confirm"]!), h);
  } finally { h.release(); }
});

test("choosing another report keeps the old claims dimmed with an updating note until the new ones arrive", async () => {
  serve("launch");
  const other = "c9999999-9999-4999-8999-999999999999";
  const h = hold();
  server.use(http.get("/v1/cases/:caseId/opposing-reports/:doc", async ({ params }) => {
    if (params.doc === other) await h.enter();
    return json(report() as never);
  }));
  const { router } = renderAt(`${url}?document=${DOC}`);
  await screen.findByText("Haemoglobin was 89 in February");
  await act(async () => { await router.navigate(`${url}?document=${other}`); });
  try {
    const note = await screen.findByText("Updating the other expert's report…");
    expect(note.closest("[role=status]")).not.toBeNull();
    expect(screen.getByText("Haemoglobin was 89 in February").closest("[aria-busy]")).toHaveAttribute("aria-busy", "true");
  } finally { h.release(); }
  await waitFor(() => expect(screen.queryByText("Updating the other expert's report…")).toBeNull());
});
