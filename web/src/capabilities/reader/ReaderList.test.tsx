// Screen 09, "What we found on this page": one list with hairline rows (not boxes), the chosen row
// opens in place into the fact card, and the next unchecked suggestion opens by itself.
import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { launchPack } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { forgetPages } from "./pageImageCache";
import { forgetSearch } from "./search";
import { CASE_ID, fact, factId } from "./testData";
import { mockReader } from "./testing";
import "./index";

beforeEach(() => {
  forgetPages();
  forgetSearch();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:page"), revokeObjectURL: vi.fn() }));
});

const PATH = `/cases/${CASE_ID}/read/118`;
const list = () => screen.findByRole("list", { name: "What we found on this page" });
const THREE = () => [fact(1), fact(2, { status: "verified" }), fact(3, { value: "Third", status: "rejected" })];

describe("The suggestions list", () => {
  test("has no arrow bar, and says how many are checked", async () => {
    mockReader(launchPack, { alreadyRead: true, facts: THREE() });
    renderAt(PATH);
    await list();
    expect(screen.queryByRole("button", { name: "Previous suggestion on this page" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Next suggestion on this page" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Suggestion \d+ of/)).not.toBeInTheDocument();
    expect(screen.getByText("2 of 3 checked")).toBeVisible();
  });

  test("each row has a status mark with a name, the kind and date, and the words in quotes", async () => {
    mockReader(launchPack, { alreadyRead: true, facts: THREE() });
    renderAt(PATH);
    const rows = within(await list()).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(within(rows[0]!).getByRole("img", { name: "Please check" })).toBeInTheDocument();
    expect(within(rows[1]!).getByRole("img", { name: "Checked by you" })).toBeInTheDocument();
    expect(within(rows[2]!).getByRole("img", { name: "Not right" })).toBeInTheDocument();
    expect(within(rows[0]!).getByText("15 May 2024")).toBeVisible();
    expect(within(rows[0]!).getByText("“Dr P consultation 15 May 2024”")).toBeVisible();
  });

  test("the chosen row opens in place into the fact card with Correct; the others stay closed", async () => {
    mockReader(launchPack, { alreadyRead: true });
    const { user } = renderAt(PATH);
    const rows = within(await list()).getAllByRole("listitem");
    expect(within(rows[0]!).getByRole("button", { name: "Correct" })).toBeVisible();
    expect(within(rows[1]!).queryByRole("button", { name: "Correct" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Correct" })).toHaveLength(1);
    await user.click(within(rows[1]!).getByRole("button", { name: /Anusol/ }));
    expect(within(rows[1]!).getByRole("button", { name: "Correct" })).toBeVisible();
    expect(within(rows[0]!).queryByRole("button", { name: "Correct" })).not.toBeInTheDocument();
    expect(screen.getByTestId(`highlight-${factId(2)}`)).toBeInTheDocument();
  });

  test("after Correct the next unchecked suggestion opens by itself", async () => {
    const api = mockReader(launchPack, { alreadyRead: true });
    const { user } = renderAt(PATH);
    const rows = within(await list()).getAllByRole("listitem");
    await user.click(within(rows[0]!).getByRole("button", { name: "Correct" }));
    await waitFor(() => expect(api.verifies).toHaveLength(1));
    const after = within(await list()).getAllByRole("listitem");
    expect(await within(after[1]!).findByRole("button", { name: "Correct" })).toBeVisible();
    expect(within(after[0]!).getByRole("img", { name: "Checked by you" })).toBeInTheDocument();
    expect(within(after[0]!).queryByRole("button", { name: "Correct" })).not.toBeInTheDocument();
  });

  test("the arrow keys move through the list", async () => {
    mockReader(launchPack, { alreadyRead: true });
    const { user } = renderAt(PATH);
    const rows = within(await list()).getAllByRole("listitem");
    within(rows[0]!).getAllByRole("button")[0]!.focus();
    await user.keyboard("{ArrowDown}");
    expect(within(rows[1]!).getByRole("button", { name: "Correct" })).toBeVisible();
    await user.keyboard("{ArrowUp}");
    expect(within(rows[0]!).getByRole("button", { name: "Correct" })).toBeVisible();
  });

  test("groups by kind with section headers only when there is more than one kind", async () => {
    mockReader(launchPack, { alreadyRead: true });
    const one = renderAt(PATH);
    await list();
    expect(screen.queryByRole("heading", { level: 3, name: /\(\d+\)/ })).not.toBeInTheDocument();
    one.unmount();

    mockReader(launchPack, { alreadyRead: true, facts: [fact(1), fact(2, { kind: "prescription" }), fact(3, { kind: "consultation", value: "Second visit" })] });
    renderAt(PATH);
    await list();
    expect(await screen.findByRole("heading", { name: /^.+ \(2\)$/ })).toBeVisible();
    expect(screen.getByRole("heading", { name: /^.+ \(1\)$/ })).toBeVisible();
  });

  test("shows nothing before the page is read", async () => {
    mockReader(launchPack);
    renderAt(PATH);
    await screen.findByRole("button", { name: "I've read this page" });
    expect(screen.queryByRole("list", { name: "What we found on this page" })).not.toBeInTheDocument();
  });
});
