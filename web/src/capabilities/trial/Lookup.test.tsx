// S25 In the witness box: works from the sealed copy on this device with no connection and no sign-in.
// It is read-only, shows both page numbers for every result, and an out-of-date copy is wiped, not shown.
import { screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi, type MockInstance } from "vitest";
import { axeViolations, renderAt } from "../../test/render";
import { memoryStore, openCopy, saveCopy, setCopyStore } from "./offline";
import { CASE, copyContent, sealed } from "./testing";
import { TRIAL_MESSAGES as M } from "./words";

const url = `/cases/${CASE}/witness-box`;
const k = (key: string) => M[`trial.${key}`]!;
const fill = (text: string, params: Record<string, string | number>) => Object.entries(params).reduce((s, [p, v]) => s.replaceAll(`{{${p}}}`, String(v)), text);
const META = { label: "My tablet", locale: "en-GB", expiresAt: "2099-07-01T09:00:00Z", kinds: { consultation: "Zz visit", prescription: "Zz medicine" } };

let fetchSpy: MockInstance<typeof fetch>;
beforeEach(() => {
  setCopyStore(memoryStore());
  fetchSpy = vi.spyOn(globalThis, "fetch");
});
afterEach(() => fetchSpy.mockRestore());

test("finds a page by either number with no connection, read-only", async () => {
  await saveCopy(await sealed(), META);
  const { user, container } = renderAt(url);
  expect(await screen.findByRole("heading", { level: 1, name: k("lookup.title") })).toBeInTheDocument();
  expect(screen.getByText(k("lookup.read_only"))).toBeInTheDocument();
  await user.type(screen.getByLabelText(k("lookup.search")), "2407");
  expect(await screen.findByText(fill(M["trial.lookup.results_other"]!, { count: 2 }))).toBeInTheDocument();
  expect(screen.getAllByText(fill(k("lookup.trial_page"), { court_bundle: "Zz binder", page: 2407 })).length).toBe(2);
  expect(screen.getAllByText(fill(k("lookup.your_page"), { page: 118 })).length).toBe(2);
  expect(screen.getByText("Zz visit")).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: /note/i })).not.toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
  expect(fetchSpy).not.toHaveBeenCalled();
});

test("a page not in the court bundle says so", async () => {
  await saveCopy(await sealed(), META);
  const { user } = renderAt(url);
  await user.type(await screen.findByLabelText(k("lookup.search")), "iron started");
  expect(await screen.findByText(fill(k("lookup.not_in_bundle"), { court_bundle: "Zz binder" }))).toBeInTheDocument();
  expect(screen.getByText(fill(k("lookup.in_report"), { para: 4 }))).toBeInTheDocument();
});

test("nothing found says so plainly", async () => {
  await saveCopy(await sealed(), META);
  const { user } = renderAt(url);
  await user.type(await screen.findByLabelText(k("lookup.search")), "qqzz");
  expect(await screen.findByText(k("lookup.nothing"))).toBeInTheDocument();
});

test("with no copy on this device it says how to get one", async () => {
  renderAt(url);
  expect(await screen.findByText(k("lookup.none"))).toBeInTheDocument();
  expect(fetchSpy).not.toHaveBeenCalled();
});

test("an out-of-date copy is wiped and not shown", async () => {
  await saveCopy(await sealed(copyContent()), { ...META, expiresAt: "2020-01-01T00:00:00Z" });
  renderAt(url);
  expect(await screen.findByText(k("lookup.expired"))).toBeInTheDocument();
  expect(screen.queryByLabelText(k("lookup.search"))).not.toBeInTheDocument();
  expect((await openCopy(CASE)).state).toBe("none");
});

test("a copy that has been tampered with does not open", async () => {
  const download = await sealed();
  const bytes = Uint8Array.from(atob(download.ciphertext), (c) => c.charCodeAt(0));
  bytes[0] = bytes[0]! ^ 1;
  await saveCopy({ ...download, ciphertext: btoa(String.fromCharCode(...bytes)) }, META);
  renderAt(url);
  expect(await screen.findByText(k("lookup.broken"))).toBeInTheDocument();
});
