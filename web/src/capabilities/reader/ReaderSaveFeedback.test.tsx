// Save change always answers: the reason shows right under the button, and a badly read page has a way out.
import { screen, within } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { forgetPages } from "./pageImageCache";
import { launchPack } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { CASE_ID, fact, queuePage } from "./testData";
import { mockReader } from "./testing";
import "./index";

beforeEach(() => {
  forgetPages();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:page"), revokeObjectURL: vi.fn() }));
});

const PATH = `/cases/${CASE_ID}/read/118`;

async function openEdit(opts: Parameters<typeof mockReader>[1] = {}) {
  const api = mockReader(launchPack, { alreadyRead: true, facts: [fact(1)], ...opts });
  const { user } = renderAt(PATH);
  const c = await screen.findByRole("article", { name: /Dr P consultation/ });
  await user.click(within(c).getByRole("button", { name: "Change a detail" }));
  return { api, user, c };
}

test("words not on the page: the alert sits inside the form, once, with a way to type out this page", async () => {
  const { api, user, c } = await openEdit();
  api.verifyStatus = 422;
  api.verifyCode = "not_in_page";
  await user.type(within(c).getByLabelText("The right words"), " x");
  await user.click(within(c).getByRole("button", { name: "Save change" }));
  const alert = await within(c).findByRole("alert");
  expect(alert).toHaveTextContent("That isn't in the words on this page.");
  expect(screen.getAllByText("That isn't in the words on this page.")).toHaveLength(1);
  const link = within(c).getByRole("link", { name: "Was the page read wrongly? Type out this page" });
  expect(link).toHaveAttribute("href", `/cases/${CASE_ID}/read/couldnt-read?page=118`);
});

test("saving with nothing changed says so", async () => {
  const { api, user, c } = await openEdit();
  await user.click(within(c).getByRole("button", { name: "Save change" }));
  expect(await within(c).findByRole("alert")).toHaveTextContent("You haven't changed anything.");
  expect(api.verifies).toHaveLength(0);
});

test("pressing Enter with no words chosen says to choose words", async () => {
  const { api, user, c } = await openEdit();
  const words = await screen.findAllByRole("button", { pressed: true });
  for (const b of words) await user.click(b);
  await user.type(within(c).getByLabelText("The right words"), " x{Enter}");
  expect(await within(c).findByRole("alert")).toHaveTextContent("Choose the words on the page that show this.");
  expect(api.verifies).toHaveLength(0);
});

test("an emptied headline says it needs words", async () => {
  const { api, user, c } = await openEdit();
  await user.clear(within(c).getByLabelText("The right words"));
  await user.click(within(c).getByRole("button", { name: "Save change" }));
  expect(await within(c).findByRole("alert")).toHaveTextContent("Write what the page shows.");
  expect(api.verifies).toHaveLength(0);
});

test("a refused timeline date shows under its own Save button", async () => {
  const api = mockReader(launchPack, { alreadyRead: true, facts: [fact(1, { status: "verified", event_date: null })] });
  const { user } = renderAt(PATH);
  const c = await screen.findByRole("article", { name: /Dr P consultation/ });
  await user.click(within(c).getByRole("button", { name: "More for this fact" }));
  await user.click(within(c).getByRole("button", { name: "Set date on timeline" }));
  api.verifyStatus = 422;
  api.verifyCode = "invalid";
  await user.click(within(c).getByRole("button", { name: "Save change" }));
  expect(await within(c).findByRole("alert")).toHaveTextContent("That isn't in the words on this page.");
});

test("a stale version (412) is still told at the top and reloads", async () => {
  const { api, user, c } = await openEdit();
  api.verifyStatus = 412;
  api.verifyCode = "stale";
  await user.type(within(c).getByLabelText("The right words"), " x");
  await user.click(within(c).getByRole("button", { name: "Save change" }));
  expect(await screen.findByText(/changed since you opened it/)).toBeVisible();
});

test("the type-out screen opens on the page asked for, even one not in the unreadable list", async () => {
  const api = mockReader(launchPack, { queue: [queuePage(612), queuePage(640)] });
  const { user } = renderAt(`/cases/${CASE_ID}/read/couldnt-read?page=118`);
  await screen.findByRole("img", { name: "Page 118" });
  await vi.waitFor(() => expect(screen.getByRole("img", { name: "Page 118" })).toHaveAttribute("src", expect.stringMatching(/^blob:/)));
  await user.type(screen.getByLabelText("What does this page show?"), "Booster given");
  await user.click(screen.getByRole("button", { name: "Save and next page" }));
  await vi.waitFor(() => expect(api.puts).toHaveLength(1));
  expect(api.puts[0]!.page).toBe(118);
});
