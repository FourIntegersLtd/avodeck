// Screens 09 and 10 as the design canvas draws them: numbers set with the person's locale, the keyboard
// help only after the read, the suggestion's details as rows, and the relevance question as Yes / No.
import { screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { fixturePack, launchPack } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { CASE_ID, fact } from "./testData";
import { mockReader } from "./testing";
import "./index";

const PATH = `/cases/${CASE_ID}/read/118`;

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("Reading, look and feel, under the $name", ({ pack }: { pack: Schemas["CasePack"] }) => {
  test("the footer sets thousands with the pack's locale: page, pages read and pages to go", async () => {
    mockReader(pack, { progress: { read: 1104, total: 1231, seconds_left: 7620 } });
    renderAt(PATH);
    const n = new Intl.NumberFormat(pack.locale);
    const text = `Page 118 of ${n.format(1231)}. You've read ${n.format(1104)}. ${n.format(127)} to go.`;
    expect(await screen.findByText(text)).toBeVisible();
  });

  test("the keyboard help is not on the side; the ? key opens it in More tools", async () => {
    mockReader(pack);
    const { user } = renderAt(PATH);
    await screen.findByRole("button", { name: "Mark as read" });
    expect(screen.queryByText("Keyboard shortcuts")).not.toBeInTheDocument();
    await user.keyboard("?");
    expect(await screen.findByRole("group", { name: "Keyboard shortcuts" })).toBeVisible();
  });

  test("a suggestion shows its details as rows, without the date already in its title", async () => {
    mockReader(pack, { alreadyRead: true, facts: [fact(1, { fields: { date: "2024-05-15", who: "Dr P, senior", how: "Face to face" } }), fact(2)] });
    renderAt(PATH);
    const card = await screen.findByRole("article", { name: /Dr P consultation/ });
    expect(within(card).getByText("Who")).toBeVisible();
    expect(within(card).getByText("Dr P, senior")).toBeVisible();
    expect(within(card).getByText("How")).toBeVisible();
    expect(within(card).queryByText("Date")).not.toBeInTheDocument();
  });

  test("a suggestion asks to be checked with a plain label, and Why not? opens on request", async () => {
    mockReader(pack, { alreadyRead: true });
    const { user } = renderAt(PATH);
    const card = await screen.findByRole("article", { name: /Dr P consultation/ });
    expect(within(card).getByText("Please check")).toBeVisible();
    const why = within(card).getByLabelText("Why not? (optional)");
    expect(why).not.toBeVisible();
    await user.click(within(card).getByText("Add a reason"));
    expect(why).toBeVisible();
  });
});

describe("Pages we couldn't read, look and feel", () => {
  test("relevance is asked as Yes or No, background only", async () => {
    mockReader(launchPack);
    renderAt(`/cases/${CASE_ID}/read/couldnt-read`);
    expect(await screen.findByRole("radio", { name: "Yes" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "No, background only" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "Relevant" })).not.toBeInTheDocument();
  });
});
