// Screen 10, Pages we couldn't read: type out each page, say if it matters, move on. Both pack sets.
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { fixturePack, launchPack } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { CASE_ID, queuePage } from "./testData";
import { mockReader } from "./testing";
import "./index";

const PATH = `/cases/${CASE_ID}/read/couldnt-read`;

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("Pages we couldn't read under the $name", ({ pack }: { pack: Schemas["CasePack"] }) => {
  test("shows the queue, the first page to do and the count", async () => {
    mockReader(pack, { queue: [queuePage(612, { done: true }), queuePage(640), queuePage(655)] });
    const { container } = renderAt(PATH);
    expect(await screen.findByRole("heading", { level: 1, name: "Pages we couldn't read" })).toBeVisible();
    expect(await screen.findByText("1 of 3 done")).toBeVisible();
    expect(screen.getByRole("button", { name: /p\.612/ })).toBeVisible();
    expect(screen.getByRole("button", { name: /p\.640/ })).toHaveAttribute("aria-current", "true");
    await waitFor(() => expect(screen.getByRole("img", { name: "Page 640" })).toHaveAttribute("src", expect.stringMatching(/^data:image\/jpeg/)));
    expect(screen.getByLabelText("What does this page show?")).toBeVisible();
    expect(screen.getByRole("link", { name: "Back to reading" })).toHaveAttribute("href", `/cases/${CASE_ID}/read`);
    expect(await axeViolations(container)).toEqual([]);
  });

  test("Save and next sends the words and relevance, then moves to the next page", async () => {
    const api = mockReader(pack);
    const { user } = renderAt(PATH);
    await user.type(await screen.findByLabelText("What does this page show?"), "Nursing note, bowel prep tolerated");
    await user.click(screen.getByRole("radio", { name: "Background only" }));
    await user.click(screen.getByRole("button", { name: "Save and next page" }));
    await waitFor(() => expect(api.puts).toHaveLength(1));
    expect(api.puts[0]).toEqual({ page: 612, body: { text: "Nursing note, bowel prep tolerated", relevance: "background" } });
    expect(await screen.findByRole("img", { name: "Page 640" })).toBeVisible();
    expect(await screen.findByText("1 of 3 done")).toBeVisible();
    expect(screen.getByLabelText("What does this page show?")).toHaveValue("");
  });

  test("Save is not offered with nothing typed", async () => {
    mockReader(pack);
    renderAt(PATH);
    expect(await screen.findByRole("button", { name: "Save and next page" })).toBeDisabled();
  });

  test("Ask for a clearer copy marks the page", async () => {
    const api = mockReader(pack);
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "I can't read it either: ask for a clearer copy" }));
    await waitFor(() => expect(api.patches).toEqual([{ page: 612, body: { needs_clearer_copy: true } }]));
    expect(await screen.findByText(/clearer copy is on your list/)).toBeVisible();
  });

  test("when re-reading is not ready (503) it says the typing is not saved yet", async () => {
    const api = mockReader(pack);
    api.putStatus = 503;
    api.putCode = "unavailable";
    const { user } = renderAt(PATH);
    await user.type(await screen.findByLabelText("What does this page show?"), "text");
    await user.click(screen.getByRole("button", { name: "Save and next page" }));
    expect(await screen.findByText(/isn't saved yet/)).toBeVisible();
    expect(screen.getByLabelText("What does this page show?")).toHaveValue("text");
  });

  test("a finished queue says so", async () => {
    mockReader(pack, { queue: [queuePage(612, { done: true })] });
    renderAt(PATH);
    expect(await screen.findByText("All done. Thank you.")).toBeVisible();
  });
});
