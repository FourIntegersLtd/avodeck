// Screen 09 as the design canvas shows it: the toolbar (Turn page, Clearer copy, Not this patient), "Why we do
// this", the related record, the progress bar and the keyboard hints. Runs under both pack sets.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { fixturePack, launchPack } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { CASE_ID, fact, factId, pageId } from "./testData";
import { mockReader } from "./testing";
import "./index";

const PATH = `/cases/${CASE_ID}/read/118`;

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("Reading, as designed, under the $name", ({ pack }: { pack: Schemas["CasePack"] }) => {
  test("Turn page turns the page a quarter and reads it again", async () => {
    const api = mockReader(pack);
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Turn page" }));
    await waitFor(() => expect(api.rotations).toEqual([{ pageId: pageId(118), body: { degrees: 90 } }]));
    expect(await screen.findByText(/We're reading the page again/)).toBeVisible();
  });

  test("Clearer copy puts the page on the list for a clearer copy", async () => {
    const api = mockReader(pack);
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Clearer copy" }));
    await waitFor(() => expect(api.patches).toEqual([{ page: 118, body: { needs_clearer_copy: true } }]));
    expect(await screen.findByText(/a clearer copy is on your list/)).toBeVisible();
  });

  test("Not this patient opens the not-this-patient screen for this page", async () => {
    mockReader(pack);
    renderAt(PATH);
    expect(await screen.findByRole("link", { name: "Not this patient" })).toHaveAttribute("href", `/cases/${CASE_ID}/bundle/not-this-patient?page=118`);
  });

  test("Why we do this explains reading first", async () => {
    mockReader(pack);
    const { user } = renderAt(PATH);
    expect(screen.queryByText(/so your own reading comes first/)).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Why we do this" }));
    expect(screen.getByText(/so your own reading comes first/)).toBeVisible();
  });

  test("a visit shows the record found with it, and whether you have checked it", async () => {
    const related = (status: string) => [{ fact_id: factId(2), kind: "prescription", value: "Anusol HC suppositories", event_date: "2024-05-15", status }];
    mockReader(pack, { alreadyRead: true, facts: [fact(1, { related: related("suggested") }), fact(2)] });
    renderAt(PATH);
    const visit = await screen.findByRole("article", { name: /Dr P consultation/ });
    expect(within(visit).getByText(/Prescription · 15 May 2024/)).toBeVisible();
    expect(within(visit).getByText(/Anusol HC suppositories · not checked yet/)).toBeVisible();
  });

  test("a related record you have checked says so", async () => {
    const related = [{ fact_id: factId(2), kind: "prescription", value: "Anusol HC suppositories", event_date: "2024-05-15", status: "suggested" }];
    mockReader(pack, { alreadyRead: true, facts: [fact(1, { related }), fact(2)] });
    const { user } = renderAt(PATH);
    const rx = await screen.findByRole("article", { name: /Anusol HC suppositories/ });
    await user.click(within(rx).getByRole("button", { name: "Correct" }));
    const visit = screen.getByRole("article", { name: /Dr P consultation/ });
    expect(await within(visit).findByText(/Anusol HC suppositories · checked by you/)).toBeVisible();
  });

  test("the keyboard hints are C, N and the arrow, and those keys work", async () => {
    const api = mockReader(pack, { alreadyRead: true, lastPage: 119 });
    const { user } = renderAt(PATH);
    await screen.findByRole("article", { name: /Dr P consultation/ });
    for (const [key, what] of [["C", "correct"], ["N", "not right"], ["→", "next page"]]) {
      expect(screen.getByText(key)).toBeVisible();
      expect(screen.getByText(new RegExp(what))).toBeVisible();
    }
    await user.keyboard("c");
    await waitFor(() => expect(api.verifies[0]).toMatchObject({ id: factId(1), body: { action: "correct" } }));
    await user.keyboard("n");
    await waitFor(() => expect(api.verifies[1]).toMatchObject({ id: factId(2), body: { action: "not_right" } }));
    await user.keyboard("{ArrowRight}");
    expect(await screen.findByRole("img", { name: "Page 119" })).toBeVisible();
  });

  test("progress is a bar with the page and the pages left, and passes axe", async () => {
    mockReader(pack);
    const { container } = renderAt(PATH);
    expect(await screen.findByRole("progressbar", { name: /Pages you've read: 3 of 10/ })).toHaveAttribute("aria-valuenow", "3");
    expect(await axeViolations(container)).toEqual([]);
  });
});
