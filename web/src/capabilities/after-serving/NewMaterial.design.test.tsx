// S21 as designed: two cards (the new pages, the other expert's report), the checked statements as a table with the
// matching ones folded away, where the two experts differ, and the way on to the experts' discussion.
import { screen, within } from "@testing-library/react";
import { beforeEach, expect, test } from "vitest";
import { registerScreen } from "../../foundation/lib/screens";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import type { Schemas } from "../../foundation/platform-client";
import { claim, DOC, P1, Q1, serve, type OpposingStub } from "./testing";

const url = `/cases/${CASE_A}/new-material?document=${DOC}`;
const page = (no: number, read: boolean, may: Schemas["NewPage"]["may_matter"] = []): Schemas["NewPage"] => ({ page_id: `p${no}`, page_no: no, read, may_matter: may });
const report = (claims: Schemas["OpposingClaimOut"][]): OpposingStub => ({
  id: "r1", document_id: DOC, received_on: "2026-12-09", checked_at: "2026-12-09T10:00:00Z", claims_found: claims.length, claims,
  positions: [{ question_id: Q1, own: [{ paragraph_id: P1, para_no: 614, text: "Ours." }], theirs: [{ paragraph_ref: "5.1", text_verbatim: "Theirs." }] }],
});
const match = (n: number) => claim({ id: `m${n}`, claim_no: n, status: "matches", discrepancy: false, needs_attention: false, text_verbatim: `Matching statement ${n}` });

beforeEach(() => registerScreen("expert_meeting", ({ caseId }) => `/cases/${caseId}/joint-statement`));

test("the pages card gives the range, how many are read and the way to read them", async () => {
  serve("launch", { pages: [page(1232, false, [{ fact_id: "f", question_id: Q1, why: "colonoscopy report" }]), page(1233, true)] });
  const { container } = renderAt(url);
  expect(await screen.findByText("2 new pages")).toBeInTheDocument();
  expect(screen.getByText("Numbered 1,232 to 1,233 · read 1 of 2")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Read the new pages" })).toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
});

test("the report card says how many statements need attention", async () => {
  serve("launch", { opposing: report([claim(), claim({ id: "c2", claim_no: 2 }), match(3)]) });
  renderAt(url);
  expect(await screen.findByText("We've checked every statement they quote against the bundle. 2 need your attention.")).toBeInTheDocument();
});

test("the statements are a table of their words, their reference and what we found; matches fold into a count", async () => {
  serve("launch", { opposing: report([claim(), match(2), match(3), match(4)]) });
  renderAt(url);
  const table = await screen.findByRole("table", { name: "What they say, checked against the records" });
  expect(within(table).getByRole("columnheader", { name: "Their statement" })).toBeInTheDocument();
  expect(within(table).getByRole("columnheader", { name: "Their reference" })).toBeInTheDocument();
  expect(within(table).getByRole("columnheader", { name: "What we found" })).toBeInTheDocument();
  expect(within(table).getByText("Haemoglobin was 89 in February")).toBeInTheDocument();
  expect(within(table).getByText("Matching statement 2")).toBeInTheDocument();
  expect(within(table).queryByText("Matching statement 3")).toBeNull();
  expect(within(table).getByText("and 2 more statements that match")).toBeInTheDocument();
});

test("where the experts differ is a list of the question, your view and theirs, with the way on to the discussion", async () => {
  serve("launch", { opposing: report([claim()]) });
  renderAt(url);
  const differ = await screen.findByRole("region", { name: "Where you and the other expert differ" });
  expect(within(differ).getByText("Ours.")).toBeInTheDocument();
  expect(within(differ).getByText("Theirs.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Prepare for the experts' discussion" })).toHaveAttribute("href", `/cases/${CASE_A}/joint-statement`);
});

test("the report card says when it arrived, beside the pages card", async () => {
  serve("launch", { pages: [page(1232, false)], opposing: report([claim()]) });
  renderAt(url);
  const pages = await screen.findByRole("region", { name: "1 new page" });
  const theirs = screen.getByRole("region", { name: "The other expert's report" });
  expect(pages.parentElement).toBe(theirs.parentElement);
  expect(within(theirs).getByText("Received 9 December")).toBeInTheDocument();
});

test("the way on to the discussion shows even when no positions are listed", async () => {
  serve("launch", { opposing: { ...report([claim()]), positions: [] } });
  renderAt(url);
  expect(await screen.findByRole("link", { name: "Prepare for the experts' discussion" })).toBeInTheDocument();
});
