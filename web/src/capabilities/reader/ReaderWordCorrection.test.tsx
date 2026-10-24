// Correcting the computer's reading: tick "These words were read wrongly" and save; "The right words" is the
// expert's reading of the chosen words, so nothing is typed twice (decision 0018).
import { screen, within } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
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
const TICK = "These words were read wrongly";
const SAYS = "What the words really say";

async function openEdit(opts: Parameters<typeof mockReader>[1] = {}) {
  const api = mockReader(launchPack, { alreadyRead: true, facts: [fact(1)], ...opts });
  const { user } = renderAt(PATH);
  const c = await screen.findByRole("article", { name: /Dr P consultation/ });
  await user.click(within(c).getByRole("button", { name: "Change a detail" }));
  await screen.findAllByRole("button", { pressed: true });
  return { api, user, c };
}

test("with words chosen, the tick box shows; ticking it asks for nothing more", async () => {
  const { c, user } = await openEdit();
  const tick = within(c).getByRole("checkbox", { name: TICK });
  expect(tick).not.toBeChecked();
  await user.click(tick);
  expect(within(c).queryByLabelText(SAYS)).toBeNull();
  expect(within(c).getAllByRole("textbox", { name: "The right words" })).toHaveLength(1);
  expect(within(c).getByText("We'll keep “The right words” as what these words really say.")).toBeVisible();
});

test("no words chosen: no tick box", async () => {
  const { c, user } = await openEdit();
  for (const b of await screen.findAllByRole("button", { pressed: true })) await user.click(b);
  expect(within(c).queryByRole("checkbox", { name: TICK })).toBeNull();
});

test("save sends The right words as the expert's reading of the chosen words", async () => {
  const { api, c, user } = await openEdit();
  await user.click(within(c).getByRole("checkbox", { name: TICK }));
  const value = within(c).getByLabelText("The right words");
  await user.clear(value);
  await user.type(value, "Dr Patel consultation");
  await user.click(within(c).getByRole("button", { name: "Save change" }));
  await vi.waitFor(() => expect(api.verifies).toHaveLength(1));
  expect(api.verifies[0]!.body).toMatchObject({
    action: "edit",
    new_value: "Dr Patel consultation",
    word_ids: ["p118w1a", "p118w1b"],
    corrected_words: { word_ids: ["p118w1a", "p118w1b"], text: "Dr Patel consultation" },
  });
});

test("ticked with the right words unchanged still saves the reading", async () => {
  const { api, c, user } = await openEdit();
  await user.click(within(c).getByRole("checkbox", { name: TICK }));
  await user.click(within(c).getByRole("button", { name: "Save change" }));
  await vi.waitFor(() => expect(api.verifies).toHaveLength(1));
  expect(api.verifies[0]!.body).toMatchObject({ corrected_words: { word_ids: ["p118w1a", "p118w1b"], text: "Dr P consultation 15 May 2024" } });
});

test("with words chosen, the form does not repeat the choose-words instruction", async () => {
  const { c } = await openEdit();
  expect(within(c).queryByText("Choose the words on the page that show this.")).toBeNull();
});

test("unticked: no corrected words are sent", async () => {
  const { api, c, user } = await openEdit();
  await user.type(within(c).getByLabelText("The right words"), " x");
  await user.click(within(c).getByRole("button", { name: "Save change" }));
  await vi.waitFor(() => expect(api.verifies).toHaveLength(1));
  expect(api.verifies[0]!.body).not.toHaveProperty("corrected_words");
});

test("words not on the page: first suggests the tick box, still offers to type out the page", async () => {
  const { api, c, user } = await openEdit();
  api.verifyStatus = 422;
  api.verifyCode = "not_in_page";
  await user.type(within(c).getByLabelText("The right words"), " x");
  await user.click(within(c).getByRole("button", { name: "Save change" }));
  expect(await within(c).findByRole("alert")).toHaveTextContent("That isn't in the words on this page.");
  const tip = within(c).getByText(/If the computer read the words wrongly, tick/);
  const link = within(c).getByRole("link", { name: "Was the page read wrongly? Type out this page" });
  expect(tip.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("a fact citing the expert's reading says Read by you", async () => {
  mockReader(launchPack, { alreadyRead: true, facts: [fact(1, { status: "edited", cites_correction: true })] });
  renderAt(PATH);
  const c = await screen.findByRole("article", { name: /Dr P consultation/ });
  expect(within(c).getByText("Read by you")).toBeVisible();
});

test("a fact without a correction does not say Read by you", async () => {
  mockReader(launchPack, { alreadyRead: true, facts: [fact(1)] });
  renderAt(PATH);
  const c = await screen.findByRole("article", { name: /Dr P consultation/ });
  expect(within(c).queryByText("Read by you")).toBeNull();
});
