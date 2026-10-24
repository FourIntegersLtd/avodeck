// S25 In the witness box: works from the sealed copy on this device with no connection and no sign-in.
// The copy opens only with the PIN chosen when it was put on the device; five wrong PINs wipe it. When
// online, it asks the status check first and wipes a copy that was removed, has ended or is unknown; with
// no network it keeps the copy (courts often have no wifi). It is read-only, shows both page numbers for
// every result, and an out-of-date copy is wiped, not shown (threat model T10).
import { screen, waitFor, within } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi, type MockInstance } from "vitest";
import { axeViolations, renderAt } from "../../test/render";
import { findCopy, memoryStore, saveCopy, setCopyStore } from "./offline";
import { CASE, copyContent, PIN, sealed, serveCopyStatus } from "./testing";
import { TRIAL_MESSAGES as M } from "./words";

const url = `/cases/${CASE}/witness-box`;
const k = (key: string) => M[`trial.${key}`]!;
const fill = (text: string, params: Record<string, string | number>) => Object.entries(params).reduce((s, [p, v]) => s.replaceAll(`{{${p}}}`, String(v)), text);
const META = { label: "My tablet", locale: "en-GB", expiresAt: "2099-07-01T09:00:00Z", kinds: { consultation: "Zz visit", prescription: "Zz medicine" } };
const SLOW = 60_000; // each PIN is stretched 600,000 times on purpose

let fetchSpy: MockInstance<typeof fetch>;
beforeEach(() => {
  setCopyStore(memoryStore());
  serveCopyStatus("downloaded");
  fetchSpy = vi.spyOn(globalThis, "fetch");
});
afterEach(() => {
  fetchSpy.mockRestore();
  vi.restoreAllMocks();
});

const asked = () => fetchSpy.mock.calls.map(([input]) => new URL(input instanceof Request ? input.url : String(input), "http://x").pathname);

async function unlock(user: UserEvent, pin = PIN) {
  const field = await screen.findByLabelText(k("lookup.pin.label"));
  await user.clear(field);
  await user.type(field, pin);
  await user.click(screen.getByRole("button", { name: k("lookup.pin.open") }));
  // The button stays off while a PIN is being tried, so a double press costs one try: wait for the answer.
  await waitFor(() => expect((screen.queryByLabelText(k("lookup.pin.label")) as HTMLInputElement | null)?.value ?? "").toBe(""), { timeout: SLOW });
}

test("finds a page by either number once the PIN is given, read-only", async () => {
  await saveCopy(await sealed(), META, PIN);
  const { user, container } = renderAt(url);
  expect(await screen.findByRole("heading", { level: 1, name: k("lookup.title") })).toBeInTheDocument();
  expect(screen.getByText(k("lookup.read_only"))).toBeInTheDocument();
  expect(await screen.findByLabelText(k("lookup.pin.label"))).toHaveAttribute("type", "password");
  expect(await axeViolations(container)).toEqual([]);
  await unlock(user);
  await user.type(await screen.findByLabelText(k("lookup.search")), "2407");
  expect(await screen.findByText(fill(M["trial.lookup.results_other"]!, { count: 2 }))).toBeInTheDocument();
  // The bundle's short name is the first letters of the pack's word for it ("Zz binder" is ZB).
  expect(screen.getAllByText(fill(k("lookup.trial_page"), { abbr: "ZB", number: "2,407" })).length).toBe(2);
  expect(screen.getAllByText(fill(k("lookup.your_page"), { page: 118 })).length).toBe(2);
  expect(screen.getByText("Zz visit")).toBeInTheDocument();
  // The count sits inside the search box, as on the board.
  expect(screen.getByText(fill(M["trial.lookup.results_other"]!, { count: 2 })).closest(".av-trial-lookup__box")).toContainElement(screen.getByLabelText(k("lookup.search")));
  expect(screen.queryByRole("textbox", { name: /note/i })).not.toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
  expect(asked()).toEqual([`/v1/cases/${CASE}/offline-packages/a9666666-6666-4666-8666-666666666666/status`]);
}, SLOW);

test("with no connection it asks nothing and the copy still opens", async () => {
  vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
  await saveCopy(await sealed(), META, PIN);
  const { user } = renderAt(url);
  await unlock(user);
  expect(await screen.findByLabelText(k("lookup.search"))).toBeInTheDocument();
  expect(fetchSpy).not.toHaveBeenCalled();
}, SLOW);

test("when the network fails the copy is kept and opens", async () => {
  serveCopyStatus("no_network");
  await saveCopy(await sealed(), META, PIN);
  const { user } = renderAt(url);
  await unlock(user);
  expect(await screen.findByLabelText(k("lookup.search"))).toBeInTheDocument();
}, SLOW);

test("when the check cannot say (signed out) the copy is kept", async () => {
  serveCopyStatus(401);
  await saveCopy(await sealed(), META, PIN);
  renderAt(url);
  expect(await screen.findByLabelText(k("lookup.pin.label"))).toBeInTheDocument();
  expect((await findCopy(CASE)).state).toBe("locked");
}, SLOW);

test.each(["revoked", "expired", 404] as const)("a copy the check calls %s is wiped and not shown", async (answer) => {
  serveCopyStatus(answer);
  await saveCopy(await sealed(), META, PIN);
  renderAt(url);
  expect(await screen.findByText(k("lookup.revoked"))).toBeInTheDocument();
  expect(screen.queryByLabelText(k("lookup.pin.label"))).not.toBeInTheDocument();
  expect((await findCopy(CASE)).state).toBe("none");
}, SLOW);

test("a wrong PIN says how many tries are left; the fifth wipes the copy", async () => {
  await saveCopy(await sealed(), META, PIN);
  const { user } = renderAt(url);
  await unlock(user, "000000");
  expect(await screen.findByText(fill(M["trial.lookup.pin.wrong_other"]!, { count: 4 }))).toBeInTheDocument();
  for (let i = 0; i < 3; i++) await unlock(user, "000000");
  expect(await screen.findByText(fill(M["trial.lookup.pin.wrong_one"]!, { count: 1 }))).toBeInTheDocument();
  await unlock(user, "000000");
  expect(await screen.findByText(k("lookup.pin.wiped"))).toBeInTheDocument();
  expect(screen.queryByLabelText(k("lookup.pin.label"))).not.toBeInTheDocument();
  expect((await findCopy(CASE)).state).toBe("none");
}, SLOW);

test("a page not in the court bundle says so", async () => {
  await saveCopy(await sealed(), META, PIN);
  const { user } = renderAt(url);
  await unlock(user);
  await user.type(await screen.findByLabelText(k("lookup.search")), "iron started");
  expect(await screen.findByText(fill(k("lookup.not_in_bundle"), { court_bundle: "Zz binder" }))).toBeInTheDocument();
  expect(screen.getByText(fill(k("lookup.in_report"), { para: 4 }))).toBeInTheDocument();
}, SLOW);

test("nothing found says so plainly", async () => {
  await saveCopy(await sealed(), META, PIN);
  const { user } = renderAt(url);
  await unlock(user);
  await user.type(await screen.findByLabelText(k("lookup.search")), "qqzz");
  expect(await screen.findByText(k("lookup.nothing"))).toBeInTheDocument();
}, SLOW);

test("with no copy on this device it says how to get one", async () => {
  renderAt(url);
  expect(await screen.findByText(k("lookup.none"))).toBeInTheDocument();
  expect(fetchSpy).not.toHaveBeenCalled();
});

test("an out-of-date copy is wiped and not shown", async () => {
  await saveCopy(await sealed(copyContent()), { ...META, expiresAt: "2020-01-01T00:00:00Z" }, PIN);
  renderAt(url);
  expect(await screen.findByText(k("lookup.expired"))).toBeInTheDocument();
  expect(screen.queryByLabelText(k("lookup.pin.label"))).not.toBeInTheDocument();
  expect((await findCopy(CASE)).state).toBe("none");
}, SLOW);

test("a copy that has been tampered with does not open", async () => {
  const download = await sealed();
  const bytes = Uint8Array.from(atob(download.ciphertext), (c) => c.charCodeAt(0));
  bytes[0] = bytes[0]! ^ 1;
  await saveCopy({ ...download, ciphertext: btoa(String.fromCharCode(...bytes)) }, META, PIN);
  const { user } = renderAt(url);
  await unlock(user);
  expect(await screen.findByText(k("lookup.broken"))).toBeInTheDocument();
}, SLOW);

test("the bar has the mark, the title and the read-only chip with its lock, and Exit, with no sign-in or case call", async () => {
  await saveCopy(await sealed(), META, PIN);
  const { container } = renderAt(url);
  expect(await screen.findByText(k("lookup.bar_title"))).toBeInTheDocument();
  const chip = screen.getByText(k("lookup.read_only")).closest(".av-trial-lookup__chip");
  expect(chip?.querySelector("svg")).not.toBeNull();
  expect(screen.getByRole("link", { name: k("lookup.exit") })).toHaveAttribute("href", `/cases/${CASE}/trial-pack`);
  expect(container.querySelector(".av-case-bar")).toBeNull();
  expect(asked()).not.toContain(`/v1/cases/${CASE}`);
});

test("the words searched for are highlighted in what the record says", async () => {
  await saveCopy(await sealed(), META, PIN);
  const { user, container } = renderAt(url);
  await unlock(user);
  await user.type(await screen.findByLabelText(k("lookup.search")), "iron started");
  await screen.findByText(fill(k("lookup.not_in_bundle"), { court_bundle: "Zz binder" }));
  expect([...container.querySelectorAll("mark")].map((m) => m.textContent?.toLowerCase())).toEqual(["iron", "started"]);
}, SLOW);

test("the bar names the case and each result is coloured by its record source", async () => {
  await saveCopy(await sealed(), META, PIN);
  const { user } = renderAt(url);
  expect(await screen.findByText(k("lookup.bar_title"))).toBeInTheDocument();
  await unlock(user);
  expect(await screen.findByText(fill(k("lookup.bar_case"), { case: "Mrs A" }))).toBeInTheDocument();
  const search = await screen.findByLabelText(k("lookup.search"));
  await user.type(search, "reported, no");
  const gp = (await screen.findByText("reported, no")).closest("li")!;
  expect(gp).toHaveStyle({ "--av-source": "#1E6B4E" });
  expect(within(gp).getByText("Zz record")).toBeInTheDocument();
  await user.clear(search);
  await user.type(search, "ferrous");
  const other = (await screen.findByText("Ferrous")).closest("li")!;
  expect(other).toHaveStyle({ "--av-source": "#4C6E91" });
  expect(within(other).getByText("Zz hospital")).toBeInTheDocument();
}, SLOW);

test("before the PIN it says when the copy ends and how many tries are left, running down", async () => {
  await saveCopy(await sealed(), META, PIN);
  const { user } = renderAt(url);
  expect(await screen.findByText(fill(k("lookup.ends"), { date: "1 July 2099" }))).toBeInTheDocument();
  expect(screen.getByText(fill(M["trial.lookup.pin.left_other"]!, { count: 5 }))).toBeInTheDocument();
  await unlock(user, "000000");
  expect(await screen.findByText(fill(M["trial.lookup.pin.left_other"]!, { count: 4 }))).toBeInTheDocument();
  for (let i = 0; i < 3; i++) await unlock(user, "000000");
  expect(await screen.findByText(fill(M["trial.lookup.pin.left_one"]!, { count: 1 }))).toBeInTheDocument();
}, SLOW);
