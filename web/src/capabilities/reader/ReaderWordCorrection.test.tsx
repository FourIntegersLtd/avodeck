// Correcting the computer's reading: tick "These words were read wrongly", type what they say, save (decision 0018).
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

test("with words chosen, the tick box shows; ticking it shows one box for what the words say", async () => {
  const { c, user } = await openEdit();
  const tick = within(c).getByRole("checkbox", { name: TICK });
  expect(tick).not.toBeChecked();
  expect(within(c).queryByLabelText(SAYS)).toBeNull();
  await user.click(tick);
  expect(within(c).getAllByLabelText(SAYS)).toHaveLength(1);
});

test("no words chosen: no tick box", async () => {
  const { c, user } = await openEdit();
  for (const b of await screen.findAllByRole("button", { pressed: true })) await user.click(b);
  expect(within(c).queryByRole("checkbox", { name: TICK })).toBeNull();
});

test("save sends the expert's reading of the chosen words with the new value", async () => {
  const { api, c, user } = await openEdit();
  await user.click(within(c).getByRole("checkbox", { name: TICK }));
  await user.type(within(c).getByLabelText(SAYS), "Dr Patel");
  const value = within(c).getByLabelText("The right words");
  await user.clear(value);
  await user.type(value, "Dr Patel consultation");
  await user.click(within(c).getByRole("button", { name: "Save change" }));
  await vi.waitFor(() => expect(api.verifies).toHaveLength(1));
  expect(api.verifies[0]!.body).toMatchObject({
    action: "edit",
    new_value: "Dr Patel consultation",
    word_ids: ["p118w1a", "p118w1b"],
    corrected_words: { word_ids: ["p118w1a", "p118w1b"], text: "Dr Patel" },
  });
});

test("ticked with nothing typed says to type what the words say", async () => {
  const { api, c, user } = await openEdit();
  await user.click(within(c).getByRole("checkbox", { name: TICK }));
  await user.type(within(c).getByLabelText("The right words"), " x");
  await user.click(within(c).getByRole("button", { name: "Save change" }));
  expect(await within(c).findByRole("alert")).toHaveTextContent("Type what the words really say.");
  expect(api.verifies).toHaveLength(0);
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
