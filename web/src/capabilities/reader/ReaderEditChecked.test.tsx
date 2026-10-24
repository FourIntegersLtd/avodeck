// Screen 09: a checked fact can still be changed (state-machines "Fact"). The server refuses a fact cited
// in a served report with 409 `served_use_supplementary`; the card says so and links to the supplementary
// report. A fact on a page replaced by a clearer copy can be moved there, citing words chosen on that copy.
// Kinds and their details use the pack's own words.
import { screen, within } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { registerScreen } from "../../foundation/lib/screens";
import { forgetPages } from "./pageImageCache";
import { launchPack } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { CASE_ID, fact } from "./testData";
import { mockReader } from "./testing";
import "./index";

beforeEach(() => {
  forgetPages();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:page"), revokeObjectURL: vi.fn() }));
});

const PATH = `/cases/${CASE_ID}/read/118`;
const checkedFact = () => fact(1, { status: "verified", version: 3 });
const at = (x0: number, y0: number) => ({ x0, y0, x1: x0 + 0.05, y1: y0 + 0.02 });
const clearerWords = [
  { word_id: "p900w1", text: "Dr", box: at(0.1, 0.1) },
  { word_id: "p900w2", text: "P", box: at(0.2, 0.1) },
];

async function card(name: RegExp) {
  return screen.findByRole("article", { name });
}

test("a checked fact offers Change a detail and saves the change with its version", async () => {
  const api = mockReader(launchPack, { alreadyRead: true, facts: [checkedFact()] });
  const { user } = renderAt(PATH);
  const c = await card(/Dr P consultation/);
  await user.click(within(c).getByRole("button", { name: "Change a detail" }));
  await user.type(within(c).getByLabelText("The right words"), " x");
  await user.click(within(c).getByRole("button", { name: "Save change" }));
  await vi.waitFor(() => expect(api.verifies).toHaveLength(1));
  expect(api.verifies[0]).toMatchObject({ ifMatch: '"3"', body: { action: "edit", new_value: "Dr P consultation 15 May 2024 x", word_ids: ["p118w1a", "p118w1b"] } });
});

test("a fact in a served report says so under Save and links to the supplementary report", async () => {
  registerScreen("S21b", ({ caseId }) => `/cases/${caseId}/correct-report`);
  const api = mockReader(launchPack, { alreadyRead: true, facts: [checkedFact()] });
  api.verifyStatus = 409;
  api.verifyCode = "served_use_supplementary";
  const { user } = renderAt(PATH);
  const c = await card(/Dr P consultation/);
  await user.click(within(c).getByRole("button", { name: "Change a detail" }));
  await user.type(within(c).getByLabelText("The right words"), " x");
  await user.click(within(c).getByRole("button", { name: "Save change" }));
  expect(await within(c).findByRole("alert")).toHaveTextContent("Your report has been served");
  expect(within(c).getByRole("link", { name: "Correct or update your report" })).toHaveAttribute("href", `/cases/${CASE_ID}/correct-report`);
  expect(within(c).getByLabelText("The right words")).toHaveValue("Dr P consultation 15 May 2024 x");
});

test("kinds and details use the pack's words", async () => {
  mockReader(launchPack, {
    alreadyRead: true,
    facts: [fact(1, { kind: "result", fields: { reference_range: "4-10" } }), fact(2, { kind: "safety_netting", fields: {} })],
  });
  renderAt(PATH);
  const c = await card(/Dr P consultation/);
  expect(within(c).getByText("Normal range")).toBeVisible();
  expect(within(c).queryByText("Reference range")).not.toBeInTheDocument();
  expect(screen.getAllByText(/Safety-netting advice/).length).toBeGreaterThan(0);
});

test("a fact on a page with a clearer copy moves there, citing words chosen on the copy", async () => {
  const api = mockReader(launchPack, { alreadyRead: true, facts: [checkedFact()], clearer: { page: 900, read: true, words: clearerWords } });
  const { user } = renderAt(PATH);
  const c = await card(/Dr P consultation/);
  await user.click(within(c).getByRole("button", { name: "More for this fact" }));
  await user.click(within(c).getByRole("button", { name: "Move to the clearer copy" }));
  expect(await screen.findByRole("img", { name: "Page 900" })).toBeInTheDocument();
  await user.click(await screen.findByRole("button", { name: "Dr" }));
  await user.click(screen.getByRole("button", { name: "P" }));
  await user.click(within(c).getByRole("button", { name: "Move here" }));
  await vi.waitFor(() => expect(api.moves).toHaveLength(1));
  expect(api.moves[0]).toMatchObject({ ifMatch: '"3"', body: { to_page_no: 900, word_ids: ["p900w1", "p900w2"] } });
});

test("moving with no words chosen says what to do and sends nothing", async () => {
  const api = mockReader(launchPack, { alreadyRead: true, facts: [checkedFact()], clearer: { page: 900, read: true, words: clearerWords } });
  const { user } = renderAt(PATH);
  const c = await card(/Dr P consultation/);
  await user.click(within(c).getByRole("button", { name: "More for this fact" }));
  await user.click(within(c).getByRole("button", { name: "Move to the clearer copy" }));
  await screen.findByRole("button", { name: "Dr" });
  await user.click(within(c).getByRole("button", { name: "Move here" }));
  expect(await within(c).findByRole("alert")).toHaveTextContent("Choose the words on the clearer copy that show this.");
  expect(api.moves).toHaveLength(0);
});

test("a clearer copy not read yet asks to read it first, with a link to it", async () => {
  const api = mockReader(launchPack, { alreadyRead: true, facts: [checkedFact()], clearer: { page: 900, read: false, words: clearerWords } });
  const { user } = renderAt(PATH);
  const c = await card(/Dr P consultation/);
  await user.click(within(c).getByRole("button", { name: "More for this fact" }));
  await user.click(within(c).getByRole("button", { name: "Move to the clearer copy" }));
  expect(await within(c).findByRole("alert")).toHaveTextContent("Read page 900 first, then come back to move this.");
  expect(within(c).getByRole("link", { name: "Open page 900" })).toHaveAttribute("href", `/cases/${CASE_ID}/read/900`);
  expect(api.moves).toHaveLength(0);
});

test("while words are chosen on the clearer copy, the turn keys leave the page underneath alone", async () => {
  const api = mockReader(launchPack, { alreadyRead: true, facts: [checkedFact()], clearer: { page: 900, read: true, words: clearerWords } });
  const { user } = renderAt(PATH);
  const c = await card(/Dr P consultation/);
  await user.click(within(c).getByRole("button", { name: "More for this fact" }));
  await user.click(within(c).getByRole("button", { name: "Move to the clearer copy" }));
  expect(await screen.findByRole("img", { name: "Page 900" })).toBeInTheDocument();
  await user.keyboard("t");
  await user.keyboard("u");
  await new Promise((r) => setTimeout(r, 50));
  expect(api.turns).toHaveLength(0);
});
