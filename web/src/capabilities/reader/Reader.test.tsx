// Screen 09, Reading: the page, "Mark as read" as the one main action, and (only after the read is
// recorded) the suggestions with the exact words highlighted. Runs under both pack sets.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { fixturePack, launchPack } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { CASE_ID, fact, factId } from "./testData";
import { mockReader } from "./testing";
import "./index";

const PATH = `/cases/${CASE_ID}/read/118`;
const card = (name: RegExp | string) => screen.getByRole("article", { name }) as HTMLElement;

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("Reading under the $name", ({ pack }: { pack: Schemas["CasePack"] }) => {
  test("an unread page shows the page, the period banner, progress and one main action, and asks for no suggestions", async () => {
    const api = mockReader(pack);
    const { container } = renderAt(PATH);
    expect(await screen.findByRole("button", { name: "Mark as read" })).toBeVisible();
    await waitFor(() => expect(screen.getByRole("img", { name: "Page 118" })).toHaveAttribute("src", expect.stringMatching(/^data:image\/jpeg;base64,/)));
    expect(screen.getByText(/You're in the period in question/)).toBeVisible();
    expect(await screen.findByText(/Pages you've read: 3 of 10/)).toBeVisible();
    expect(screen.getByText(/about 5 minutes left/)).toBeVisible();
    expect(screen.getByText(/Mark this page as read to see our suggestions/)).toBeVisible();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(api.factRequests).toBe(0);
    expect(await axeViolations(container)).toEqual([]);
  });

  test("Mark as read records the seconds, then shows the suggestions with their words highlighted", async () => {
    const api = mockReader(pack);
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Mark as read" }));
    expect(await screen.findByRole("article", { name: /Dr P consultation 15 May 2024/ })).toBeVisible();
    expect(api.reads).toHaveLength(1);
    expect(api.reads[0]!.page).toBe(118);
    expect(typeof api.reads[0]!.body.dwell_seconds).toBe("number");
    expect(api.factRequests).toBeGreaterThan(0);
    const box = screen.getByTestId(`highlight-${factId(1)}`);
    expect(box.style.left).toBe("6%");
    expect(box.style.top).toBe("20%");
    expect(box.style.width).toBe("50%");
    expect(box.style.height).toBeTruthy();
    expect(screen.getByRole("button", { name: "Next page" })).toBeVisible();
  });

  test("a page that is already read shows its suggestions straight away", async () => {
    mockReader(pack, { alreadyRead: true });
    renderAt(PATH);
    expect(await screen.findByRole("article", { name: /Anusol HC suppositories/ })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Mark as read" })).not.toBeInTheDocument();
  });

  test("a 423 from the server shows no suggestions", async () => {
    const api = mockReader(pack, { pageSaysRead: true }); // the page says read, the server's blind-read record disagrees
    renderAt(PATH);
    await waitFor(() => expect(api.factRequests).toBeGreaterThan(0));
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(screen.getByText(/Mark this page as read to see our suggestions/)).toBeVisible();
  });

  test("Correct sends the version it loaded in If-Match", async () => {
    const api = mockReader(pack, { alreadyRead: true });
    const { user } = renderAt(PATH);
    const c = await screen.findByRole("article", { name: /Dr P consultation/ });
    await user.click(within(c).getByRole("button", { name: "Correct" }));
    await waitFor(() => expect(api.verifies).toHaveLength(1));
    expect(api.verifies[0]).toMatchObject({ id: factId(1), ifMatch: '"1"', body: { action: "correct" } });
    expect(await within(card(/Dr P consultation/)).findByText("Checked by you")).toBeVisible();
  });

  test("Not right sends the short reason, keeps the card, and Undo checks it again", async () => {
    const api = mockReader(pack, { alreadyRead: true });
    const { user } = renderAt(PATH);
    const c = await screen.findByRole("article", { name: /Dr P consultation/ });
    await user.type(within(c).getByLabelText("Why not? (optional)"), "wrong day");
    await user.click(within(c).getByRole("button", { name: "Not right" }));
    await waitFor(() => expect(api.verifies).toHaveLength(1));
    expect(api.verifies[0]!.body).toEqual({ action: "not_right", reason: "wrong day" });
    const rejected = await screen.findByRole("button", { name: "Undo" });
    expect(card(/Dr P consultation/)).toContainElement(rejected);
    await user.click(rejected);
    await waitFor(() => expect(api.verifies).toHaveLength(2));
    expect(api.verifies[1]).toMatchObject({ ifMatch: '"2"', body: { action: "correct" } });
  });

  test("Change a detail sends the new value with the cited words", async () => {
    const api = mockReader(pack, { alreadyRead: true });
    const { user } = renderAt(PATH);
    const c = await screen.findByRole("article", { name: /Dr P consultation/ });
    await user.click(within(c).getByRole("button", { name: "Change a detail" }));
    const input = within(c).getByLabelText("The right words");
    await user.clear(input);
    await user.type(input, "Dr P visit");
    await user.click(within(c).getByRole("button", { name: "Save change" }));
    await waitFor(() => expect(api.verifies).toHaveLength(1));
    expect(api.verifies[0]).toMatchObject({ ifMatch: '"1"', body: { action: "edit", new_value: "Dr P visit", word_ids: ["p118w1a", "p118w1b"] } });
  });

  test("words that are not on the page are refused in plain words (422)", async () => {
    const api = mockReader(pack, { alreadyRead: true });
    api.verifyStatus = 422;
    api.verifyCode = "not_in_page";
    const { user } = renderAt(PATH);
    const c = await screen.findByRole("article", { name: /Dr P consultation/ });
    await user.click(within(c).getByRole("button", { name: "Change a detail" }));
    await user.type(within(c).getByLabelText("The right words"), " x");
    await user.click(within(c).getByRole("button", { name: "Save change" }));
    expect(await screen.findByText("That isn't in the words on this page.")).toBeVisible();
  });

  test("a stale version (412) reloads the suggestions and says so", async () => {
    const api = mockReader(pack, { alreadyRead: true });
    api.verifyStatus = 412;
    api.verifyCode = "stale";
    const { user } = renderAt(PATH);
    const c = await screen.findByRole("article", { name: /Dr P consultation/ });
    const before = api.factRequests;
    await user.click(within(c).getByRole("button", { name: "Correct" }));
    expect(await screen.findByText(/changed since you opened it/)).toBeVisible();
    await waitFor(() => expect(api.factRequests).toBeGreaterThan(before));
  });

  test("a checked suggestion can go to Points against and be taken back", async () => {
    const api = mockReader(pack, { alreadyRead: true, facts: [fact(1, { status: "verified" }), fact(2)] });
    const { user } = renderAt(PATH);
    const c = await screen.findByRole("article", { name: /Dr P consultation/ });
    expect(within(card(/Anusol/)).queryByRole("button", { name: /Points against/ })).not.toBeInTheDocument();
    await user.click(within(c).getByRole("button", { name: "Send to Points against" }));
    await waitFor(() => expect(api.flags).toEqual([{ id: factId(1), method: "POST" }]));
    await user.click(await within(card(/Dr P consultation/)).findByRole("button", { name: "Take out of Points against" }));
    await waitFor(() => expect(api.flags[1]).toEqual({ id: factId(1), method: "DELETE" }));
  });

  test("keys: R marks read, 1/2/3 act on the focused suggestion, J and K move between pages", async () => {
    const api = mockReader(pack);
    const { user, router } = renderAt(PATH);
    await screen.findByRole("button", { name: "Mark as read" });
    await user.keyboard("r");
    await screen.findByRole("article", { name: /Dr P consultation/ });
    expect(api.reads).toHaveLength(1);
    await user.keyboard("1");
    await waitFor(() => expect(api.verifies[0]?.body).toEqual({ action: "correct" }));
    await user.keyboard("3");
    expect(await screen.findByLabelText("The right words")).toBeVisible();
    await user.keyboard("{Escape}");
    await user.keyboard("j");
    await waitFor(() => expect(router.state.location.pathname).toBe(`/cases/${CASE_ID}/read/119`));
    await user.keyboard("k");
    await waitFor(() => expect(router.state.location.pathname).toBe(`/cases/${CASE_ID}/read/118`));
  });

  test("keys are ignored while typing in a box", async () => {
    const api = mockReader(pack, { alreadyRead: true });
    const { user, router } = renderAt(PATH);
    const c = await screen.findByRole("article", { name: /Dr P consultation/ });
    await user.click(within(c).getByLabelText("Why not? (optional)"));
    await user.keyboard("jk12r");
    expect(api.verifies).toHaveLength(0);
    expect(router.state.location.pathname).toBe(PATH);
    fireEvent.keyDown(document.body, { key: "2" });
    await waitFor(() => expect(api.verifies).toHaveLength(1));
  });

  test("every action has a button, and a link to the pages we couldn't read", async () => {
    mockReader(pack, { alreadyRead: true });
    renderAt(PATH);
    const c = await screen.findByRole("article", { name: /Dr P consultation/ });
    for (const name of ["Correct", "Not right", "Change a detail"]) expect(within(c).getByRole("button", { name })).toBeVisible();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeVisible();
    expect(await screen.findByRole("link", { name: /3 pages we couldn't read/ })).toHaveAttribute("href", `/cases/${CASE_ID}/read/couldnt-read`);
  });

  test("without a page number it opens the first page not yet read", async () => {
    mockReader(pack, { page: 77 });
    const { router } = renderAt(`/cases/${CASE_ID}/read`);
    await waitFor(() => expect(router.state.location.pathname).toBe(`/cases/${CASE_ID}/read/77`));
  });
});
