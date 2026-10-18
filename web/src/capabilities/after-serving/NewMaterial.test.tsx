// S21 New records and the other expert's report: the new pages (may-matter only where the API returns it), the
// check, registering their report, and each statement with both quotes and its page. Nothing is called a
// difference until the expert confirms it.
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { launchWords } from "../setup/testing";
import { AFTER_MESSAGES as M } from "./words";
import { claim, CLAIM, DOC, P1, Q1, serve, type PackRun } from "./testing";
import type { Schemas } from "../../foundation/platform-client";

const url = `/cases/${CASE_A}/new-material`;
const page = (no: number, read: boolean, may: Schemas["NewPage"]["may_matter"] = []): Schemas["NewPage"] => ({ page_id: `p${no}`, page_no: no, read, may_matter: may });
const report = (claims: Schemas["app__features__after_serving__routes_new_material__ClaimOut"][] = [claim()]): Schemas["OpposingReport"] => ({
  id: "r1", document_id: DOC, received_on: "2026-12-09", checked_at: "2026-12-09T10:00:00Z", claims_found: claims.length, claims,
  positions: [{ question_id: Q1, own: [{ paragraph_id: P1, para_no: 614, text: "Our paragraph words." }], theirs: [{ paragraph_ref: "5.1", text_verbatim: "Their paragraph words." }] }],
});

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("counts the new pages and passes axe", async () => {
    serve(run, { pages: [page(1232, false), page(1233, true)] });
    const { container } = renderAt(url);
    expect(await screen.findByRole("heading", { level: 2, name: M["after_serving.new.title"] })).toBeInTheDocument();
    expect(await screen.findByText("2 new pages")).toBeInTheDocument();
    expect(screen.getByText("Read 1 of 2")).toBeInTheDocument();
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("may-matter appears only where the server returned it", async () => {
  serve("launch", { pages: [page(1232, false, [{ fact_id: "f1", question_id: Q1, why: "result bears on question 2" }]), page(1233, false)] });
  renderAt(url);
  expect(await screen.findByText("May matter: result bears on question 2 (page 1232)")).toBeInTheDocument();
  expect(screen.getAllByText(/May matter/)).toHaveLength(1);
});

test("with nothing new it says so", async () => {
  serve("launch", { pages: [] });
  renderAt(url);
  expect(await screen.findByText(M["after_serving.new.none"]!)).toBeInTheDocument();
});

test("the check is queued and says it is working", async () => {
  const { sent, record } = serve("launch", { pages: [page(1232, false)] });
  server.use(http.post("/v1/cases/:caseId/new-since-served/check", async ({ request }) => (await record(request), json({ job_id: "j" }, 202))));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: M["after_serving.new.check"]! }));
  await waitFor(() => expect(sent[0]).toMatchObject({ method: "POST", path: `/v1/cases/${CASE_A}/new-since-served/check` }));
  expect(await screen.findByText(M["after_serving.new.checking"]!)).toBeInTheDocument();
});

test("their report is registered from the file just added, with the day it arrived", async () => {
  const { sent, record } = serve("launch", { opposing: null });
  server.use(http.post("/v1/cases/:caseId/opposing-reports", async ({ request }) => (await record(request), json({ id: "r1", job_id: "j" }, 202))));
  const { user } = renderAt(`${url}?document=${DOC}`);
  await user.type(await screen.findByLabelText(M["after_serving.new.received_on"]!), "2026-12-09");
  await user.click(screen.getByRole("button", { name: M["after_serving.new.register"]! }));
  await waitFor(() => expect(sent[0]).toMatchObject({ method: "POST", body: { document_id: DOC, received_on: "2026-12-09" } }));
  expect(await screen.findByText(M["after_serving.new.registering"]!)).toBeInTheDocument();
});

test("without a file there is nothing to register", async () => {
  serve("launch", { opposing: null });
  renderAt(url);
  expect(await screen.findByText(M["after_serving.new.opposing_document_needed"]!)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: M["after_serving.new.register"]! })).toBeNull();
});

test("each statement shows their words and page next to what our page says, and the two sides side by side", async () => {
  serve("launch", { opposing: report() });
  renderAt(`${url}?document=${DOC}`);
  expect(await screen.findByText("Haemoglobin was 89 in February")).toBeInTheDocument();
  expect(screen.getByText("p.214")).toBeInTheDocument();
  expect(screen.getByText(M["after_serving.new.status.wrong_value"]!)).toBeInTheDocument();
  expect(screen.getByText("What they quote")).toBeInTheDocument();
  expect(screen.getByText("What the page says (page 214)")).toBeInTheDocument();
  expect(screen.getByText("Our paragraph words.")).toBeInTheDocument();
  expect(screen.getByText("Their paragraph words.")).toBeInTheDocument();
});

test("a difference is only called one after you confirm it, and confirming sends the version it saw", async () => {
  const { sent, record } = serve("launch", { opposing: report([claim({ version: 3 })]) });
  server.use(http.patch("/v1/cases/:caseId/opposing-claims/:id", async ({ request }) => (await record(request), json(claim({ expert_confirmed: true, version: 4 })))));
  const { user } = renderAt(`${url}?document=${DOC}`);
  await user.click(await screen.findByRole("button", { name: M["after_serving.new.confirm"]! }));
  await waitFor(() => expect(sent[0]).toMatchObject({ method: "PATCH", path: `/v1/cases/${CASE_A}/opposing-claims/${CLAIM}`, ifMatch: '"3"', body: { confirm: true } }));
  expect(await screen.findByText(M["after_serving.new.confirmed"]!)).toBeInTheDocument();
});

test("a statement that matches has nothing to confirm", async () => {
  serve("launch", { opposing: report([claim({ status: "matches", discrepancy: false })]) });
  renderAt(`${url}?document=${DOC}`);
  expect(await screen.findByText(M["after_serving.new.status.matches"]!)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: M["after_serving.new.confirm"]! })).toBeNull();
});

test("a statement that hasn't been checked yet says so and can't be confirmed", async () => {
  serve("launch", { opposing: report([claim({ status: "unchecked", discrepancy: false })]) });
  renderAt(`${url}?document=${DOC}`);
  expect(await screen.findByText(M["after_serving.new.status.unchecked"]!)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: M["after_serving.new.confirm"]! })).toBeNull();
});

test("a refused confirmation (not checked) is explained", async () => {
  serve("launch", { opposing: report([claim()]) });
  server.use(http.patch("/v1/cases/:caseId/opposing-claims/:id", () => apiError(409, "not_checked")));
  const { user } = renderAt(`${url}?document=${DOC}`);
  await user.click(await screen.findByRole("button", { name: M["after_serving.new.confirm"]! }));
  expect(await screen.findByText(M["after_serving.new.error.not_checked"]!)).toBeInTheDocument();
});
