// S06: every action shows it is working and ignores a second click while it runs.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, HttpResponse, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { byName, expectBusyOnce, hold } from "./busyTesting";
import { casePack, inCase, note, question, questions } from "./testing";

function asked(over: Parameters<typeof questions>[0] = {}, q: Parameters<typeof question>[0] = {}) {
  inCase("launch");
  const kind = String(casePack("launch").question_types[0]!.key);
  const body = questions({ questions: [question({ kind, ...q })], ...over });
  server.use(http.get("/v1/cases/:caseId/questions", () => json(body)));
}
const url = `/cases/${CASE_A}/questions`;

test("confirming everything goes busy and sends once", async () => {
  const h = hold();
  asked({}, { status: "confirmed" });
  server.use(
    http.get("/v1/cases/:caseId/questions", () => json(questions({ questions: [question({ status: "confirmed" })], assumptions: [] }))),
    http.post("/v1/cases/:caseId/questions/confirm-all", async () => (await h.enter(), json({}))),
  );
  const { user } = renderAt(url);
  try {
    await expectBusyOnce(user, await byName("Confirm questions"), h);
  } finally { h.release(); }
});

test("reading the letter again goes busy and sends once", async () => {
  const h = hold();
  asked({ read_failed: true });
  server.use(http.post("/v1/cases/:caseId/questions/read-again", async () => (await h.enter(), json({}))));
  const { user } = renderAt(url);
  try {
    await expectBusyOnce(user, await byName("Read the letter again"), h);
  } finally { h.release(); }
});

test("drafting a note goes busy and sends once", async () => {
  const h = hold();
  asked({}, { outside_expertise: true, outside_expertise_source: "expert" });
  server.use(http.post("/v1/cases/:caseId/instructing-party-notes/draft", async () => (await h.enter(), json(note("outside_expertise")))));
  const { user } = renderAt(url);
  try {
    await expectBusyOnce(user, await screen.findByRole("button", { name: /^Draft a note/ }), h);
  } finally { h.release(); }
});

test("confirming one question goes busy and sends once", async () => {
  const h = hold();
  asked();
  server.use(http.patch("/v1/cases/:caseId/questions/:id", async () => (await h.enter(), HttpResponse.json(question({ status: "confirmed", version: 2 }), { headers: { etag: '"2"' } }))));
  const { user } = renderAt(url);
  try {
    const card = within(await screen.findByRole("article", { name: "Question 1" }));
    await expectBusyOnce(user, card.getByRole("button", { name: "Confirm" }), h);
  } finally { h.release(); }
});

test("changing the kind of question disables the choices while it saves", async () => {
  const h = hold();
  asked();
  const other = String(casePack("launch").question_types[1]!.key);
  server.use(http.patch("/v1/cases/:caseId/questions/:id", async () => (await h.enter(), HttpResponse.json(question({ kind: other, version: 2 }), { headers: { etag: '"2"' } }))));
  const { user } = renderAt(url);
  try {
    const card = within(await screen.findByRole("article", { name: "Question 1" }));
    await user.click(card.getByRole("button", { name: "Edit wording" }));
    const kind = card.getByLabelText("What kind of question is this?");
    await user.selectOptions(kind, other);
    await waitFor(() => expect(kind).toBeDisabled());
    expect(kind).toHaveAttribute("aria-busy", "true");
    expect(card.getByLabelText("This may be outside my expertise")).toBeDisabled();
    expect(card.getByRole("button", { name: "Save the wording" })).toBeDisabled();
  } finally { h.release(); }
});

test("flagging a question as outside your field disables the choices while it saves", async () => {
  const h = hold();
  asked();
  server.use(http.patch("/v1/cases/:caseId/questions/:id", async () => (await h.enter(), HttpResponse.json(question({ outside_expertise: true, version: 2 }), { headers: { etag: '"2"' } }))));
  const { user } = renderAt(url);
  try {
    const card = within(await screen.findByRole("article", { name: "Question 1" }));
    await user.click(card.getByRole("button", { name: "Edit wording" }));
    const box = card.getByLabelText("This may be outside my expertise");
    await user.click(box);
    await waitFor(() => expect(box).toBeDisabled());
    expect(box).toHaveAttribute("aria-busy", "true");
  } finally { h.release(); }
});
