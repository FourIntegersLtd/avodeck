// S20: every action shows it is working and ignores a second click while it runs.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { AFTER_MESSAGES as M } from "./words";
import { byName, expectBusyOnce, hold } from "./busyTesting";
import { DOC, partyName, question, round, serve } from "./testing";

const url = `/cases/${CASE_A}/written-questions`;

test("reading a set goes busy and sends once", async () => {
  serve("launch", { rounds: [round({ extracted_at: null })], questions: [] });
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/written-questions/import", async () => (await h.enter(), json({ job_id: "j1" }, 202))));
  const { user } = renderAt(url);
  try {
    await expectBusyOnce(user, await byName(M["after_serving.questions.read"]!), h);
  } finally { h.release(); }
});

test("changing the classification disables the choice while it saves, and sends once", async () => {
  serve("launch");
  const h = hold();
  server.use(http.patch("/v1/cases/:caseId/written-questions/:q", async () => (await h.enter(), json(question({ classification: "goes_beyond", version: 2 })))));
  const { user } = renderAt(url);
  try {
    await user.click(await byName(M["after_serving.questions.change"]!));
    const select = await screen.findByLabelText(M["after_serving.questions.classification"]!);
    await user.selectOptions(select, "goes_beyond");
    expect(select).toBeDisabled();
    expect(select).toHaveAttribute("aria-busy", "true");
    expect(h.count()).toBe(1);
  } finally { h.release(); }
});

test("saving an answer goes busy and sends once", async () => {
  serve("launch");
  const h = hold();
  server.use(http.patch("/v1/cases/:caseId/written-questions/:q", async () => (await h.enter(), json(question({ answer_text: "Mine", version: 2 })))));
  const { user } = renderAt(url);
  try {
    await user.type(await screen.findByLabelText(M["after_serving.questions.answer"]!), "Mine");
    await expectBusyOnce(user, screen.getByRole("button", { name: M["after_serving.questions.answer_save"]! }), h);
  } finally { h.release(); }
});

test("keeping my version goes busy and sends once", async () => {
  serve("launch");
  const h = hold();
  let first = true;
  server.use(http.patch("/v1/cases/:caseId/written-questions/:q", async () => {
    if (first) { first = false; return apiError(412, "stale"); }
    await h.enter();
    return json(question({ answer_text: "Mine", version: 3 }));
  }));
  const { user } = renderAt(url);
  try {
    await user.type(await screen.findByLabelText(M["after_serving.questions.answer"]!), "Mine");
    await user.click(screen.getByRole("button", { name: M["after_serving.questions.answer_save"]! }));
    const dialog = await screen.findByRole("dialog", { name: M["after_serving.stale_title"] });
    await expectBusyOnce(user, within(dialog).getByRole("button", { name: M["after_serving.stale_mine"]! }), h);
  } finally { h.release(); }
});

test("telling the instructing party goes busy and sends once", async () => {
  serve("launch");
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/written-questions/:q/tell-instructing-party", async () => (await h.enter(), json({ key: "after_serving.questions.tell_note", params: { round: 1, number: 1, para_ref: "6.12" } }))));
  const { user } = renderAt(url);
  try {
    await expectBusyOnce(user, await byName(`Tell the ${partyName("launch")}`), h);
  } finally { h.release(); }
});

test("adding a set goes busy and sends once", async () => {
  serve("launch", { rounds: [], questions: [] });
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/written-question-rounds", async () => (await h.enter(), json(round(), 201))));
  const { user } = renderAt(`${url}?document=${DOC}`);
  try {
    await user.click(await byName(M["after_serving.questions.add"]!));
    await user.type(await screen.findByLabelText(M["after_serving.questions.received_on"]!), "2026-12-01");
    await user.type(screen.getByLabelText(M["after_serving.questions.answers_due_on"]!), "2026-12-26");
    await expectBusyOnce(user, screen.getByRole("button", { name: M["after_serving.questions.add_submit"]! }), h);
  } finally { h.release(); }
});

test("signing the answers goes busy and sends once", async () => {
  serve("launch", { questions: [question({ answer_text: "Done", status: "answering" })] });
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/written-questions/answers/sign", async () => (await h.enter(), json({ version: { id: "v9", display_name: "A", status: "locked", type: "answers", version_no: 2, locked_at: "2026-12-20T10:00:00Z", prev_version_id: null, sha256: "a" } }))));
  const { user } = renderAt(url);
  try {
    await user.click(await byName(M["after_serving.questions.sign_open"]!));
    await user.click(await screen.findByRole("checkbox", { name: M["after_serving.questions.confirm"]! }));
    await expectBusyOnce(user, screen.getByRole("button", { name: M["after_serving.questions.sign"]! }), h);
  } finally { h.release(); }
});
