// Screen 10: typing out a page starts from its current reading (the expert's corrections in place, from
// the words endpoint, which answers only once the page is read), so only what is wrong needs fixing.
// Save and next never greys out silently: it says why it cannot save yet.
import { screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { forgetPages } from "./pageImageCache";
import { launchPack } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { CASE_ID, queuePage } from "./testData";
import { mockReader } from "./testing";
import "./index";

beforeEach(() => {
  forgetPages();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:page"), revokeObjectURL: vi.fn() }));
});

const AT_118 = `/cases/${CASE_ID}/read/couldnt-read?page=118`;
const READING = "Dr P visit Tetanus booster given";

test("a read page starts from its current reading", async () => {
  mockReader(launchPack, { alreadyRead: true, reading: READING, queue: [queuePage(612)] });
  renderAt(AT_118);
  await waitFor(() => expect(screen.getByLabelText("What does this page show?")).toHaveValue(READING));
});

test("a page not read yet starts empty", async () => {
  mockReader(launchPack, { alreadyRead: false, queue: [queuePage(612)] });
  renderAt(AT_118);
  const box = await screen.findByLabelText("What does this page show?");
  await new Promise((r) => setTimeout(r, 50));
  expect(box).toHaveValue("");
});

test("saving the reading unchanged says why and sends nothing", async () => {
  const api = mockReader(launchPack, { alreadyRead: true, reading: READING, queue: [queuePage(612)] });
  const { user } = renderAt(AT_118);
  await waitFor(() => expect(screen.getByLabelText("What does this page show?")).toHaveValue(READING));
  await user.click(screen.getByRole("button", { name: "Save and next page" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Change the words that were read wrongly, then save.");
  expect(api.puts).toHaveLength(0);
});

test("a fixed reading is saved", async () => {
  const api = mockReader(launchPack, { alreadyRead: true, reading: READING, queue: [queuePage(612)] });
  const { user } = renderAt(AT_118);
  const box = screen.getByLabelText.bind(screen);
  await waitFor(() => expect(box("What does this page show?")).toHaveValue(READING));
  await user.type(box("What does this page show?"), " today");
  await user.click(screen.getByRole("button", { name: "Save and next page" }));
  await vi.waitFor(() => expect(api.puts).toHaveLength(1));
  expect(api.puts[0]).toMatchObject({ page: 118, body: { text: `${READING} today` } });
});
