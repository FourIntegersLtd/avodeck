// Changing a detail: the pack's date detail is a calendar day, so the only date in the form is "Date on
// timeline" (prefilled from the date as written). It goes in the same request as Save change, both as the
// timeline date and as the date detail, so the server never gets the date as written back (it refuses it).
import { screen, within } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { forgetPages } from "./pageImageCache";
import { fixturePack, launchPack } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { CASE_ID, fact } from "./testData";
import { mockReader } from "./testing";
import "./index";

beforeEach(() => {
  forgetPages();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:page"), revokeObjectURL: vi.fn() }));
});

const PATH = `/cases/${CASE_ID}/read/118`;
const misread = () => fact(1, { event_date: null, fields: { date: "31.12.93", plan: "Microgynon cont 6/12" }, value: "31.12.93 -Pitt Pill check" });

async function openEdit() {
  const api = mockReader(launchPack, { alreadyRead: true, facts: [misread()] });
  const { user } = renderAt(PATH);
  const c = await screen.findByRole("article", { name: /Pitt Pill/ });
  await user.click(within(c).getByRole("button", { name: "Change a detail" }));
  return { api, user, c };
}

test("the edit form shows Date on timeline prefilled from the printed date", async () => {
  const { c } = await openEdit();
  expect(within(c).getByLabelText("Date on timeline")).toHaveValue("1993-12-31");
});

test("the date as written is not a second box: the calendar is the only date", async () => {
  const { c } = await openEdit();
  expect(within(c).queryByLabelText("Date")).toBeNull();
  expect(within(c).getByLabelText("Plan")).toBeVisible();
});

test("changing another detail sends the date detail as the calendar day", async () => {
  const { api, c, user } = await openEdit();
  await user.type(within(c).getByLabelText("Plan"), " x");
  await user.click(within(c).getByRole("button", { name: "Save change" }));
  await vi.waitFor(() => expect(api.verifies).toHaveLength(1));
  expect(api.verifies[0]!.body).toMatchObject({ action: "edit", fields: { date: "1993-12-31", plan: "Microgynon cont 6/12 x" } });
});

test("a day picked on the calendar is the timeline date and the date detail", async () => {
  const { api, c, user } = await openEdit();
  const cal = within(c).getByLabelText("Date on timeline");
  await user.clear(cal);
  await user.type(cal, "1993-11-03");
  await user.click(within(c).getByRole("button", { name: "Save change" }));
  await vi.waitFor(() => expect(api.verifies).toHaveLength(1));
  expect(api.verifies[0]!.body).toMatchObject({ action: "edit", event_date: "1993-11-03", fields: { date: "1993-11-03" } });
});

test("details the kind does not have are not sent back", async () => {
  const api = mockReader(launchPack, { alreadyRead: true, facts: [fact(1, { event_date: null, fields: { date: "31.12.93", plan: "Microgynon", stray: "x" }, value: "31.12.93 -Pitt Pill check" })] });
  const { user } = renderAt(PATH);
  const c = await screen.findByRole("article", { name: /Pitt Pill/ });
  await user.click(within(c).getByRole("button", { name: "Change a detail" }));
  await user.type(within(c).getByLabelText("Plan"), " x");
  await user.click(within(c).getByRole("button", { name: "Save change" }));
  await vi.waitFor(() => expect(api.verifies).toHaveLength(1));
  expect(api.verifies[0]!.body.fields).not.toHaveProperty("stray");
});

test("an emptied calendar sends no timeline date", async () => {
  const { api, c, user } = await openEdit();
  await user.clear(within(c).getByLabelText("Date on timeline"));
  const plan = within(c).getByLabelText("Plan");
  await user.type(plan, " x");
  await user.click(within(c).getByRole("button", { name: "Save change" }));
  await vi.waitFor(() => expect(api.verifies).toHaveLength(1));
  expect(api.verifies[0]!.body).not.toHaveProperty("event_date");
});

test("a fact kind without a date field gets no calendar", async () => {
  mockReader(fixturePack, { alreadyRead: true, facts: [fact(1, { kind: "session", event_date: null, fields: { note: "abc" }, value: "Session note" })] });
  const { user } = renderAt(PATH);
  const c = await screen.findByRole("article", { name: /Session note/ });
  await user.click(within(c).getByRole("button", { name: "Change a detail" }));
  expect(within(c).getByLabelText("The right words")).toBeVisible();
  expect(within(c).queryByLabelText("Date on timeline")).not.toBeInTheDocument();
});
