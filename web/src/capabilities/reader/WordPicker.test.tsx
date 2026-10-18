// Screen 09, choosing the words a changed fact cites (rule 2). In "Change a detail" the page's words
// become toggles over the picture; Save sends the chosen ids. A "Please check" fact must cite words
// chosen again, so Save waits for a choice. Runs under both pack sets.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { fixturePack, launchPack } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { CASE_ID, fact } from "./testData";
import { mockReader } from "./testing";
import "./index";

const PATH = `/cases/${CASE_ID}/read/118`;
const word = (name: string) => screen.getByRole("button", { name });

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("Choosing words under the $name", ({ pack }: { pack: Schemas["CasePack"] }) => {
  test("Change a detail shows the page's words, the cited ones chosen, and Save sends the words chosen", async () => {
    const api = mockReader(pack, { alreadyRead: true });
    const { user, container } = renderAt(PATH);
    const c = await screen.findByRole("article", { name: /Dr P consultation/ });
    await user.click(within(c).getByRole("button", { name: "Change a detail" }));
    await waitFor(() => expect(word("visit")).toBeVisible());
    expect(api.wordRequests).toBeGreaterThan(0);
    expect(word("Dr")).toHaveAttribute("aria-pressed", "true");
    expect(word("P")).toHaveAttribute("aria-pressed", "true");
    expect(word("visit")).toHaveAttribute("aria-pressed", "false");
    expect(word("visit").style.left).toBe("20%");
    expect(word("Typed")).toHaveAttribute("aria-pressed", "false");

    await user.click(word("visit"));
    await user.click(word("P"));
    expect(word("visit")).toHaveAttribute("aria-pressed", "true");
    expect(word("P")).toHaveAttribute("aria-pressed", "false");
    const input = within(c).getByLabelText("The right words");
    await user.clear(input);
    await user.type(input, "Dr visit");
    await user.click(within(c).getByRole("button", { name: "Save change" }));
    await waitFor(() => expect(api.verifies).toHaveLength(1));
    expect(api.verifies[0]!.body).toEqual({ action: "edit", new_value: "Dr visit", word_ids: ["p118w1a", "p118w9"] });
    expect(await axeViolations(container)).toEqual([]);
  });

  test("a word is chosen from the keyboard", async () => {
    const api = mockReader(pack, { alreadyRead: true });
    const { user } = renderAt(PATH);
    const c = await screen.findByRole("article", { name: /Dr P consultation/ });
    await user.click(within(c).getByRole("button", { name: "Change a detail" }));
    await waitFor(() => expect(word("visit")).toBeVisible());
    word("visit").focus();
    await user.keyboard(" ");
    expect(word("visit")).toHaveAttribute("aria-pressed", "true");
    await user.click(within(c).getByRole("button", { name: "Save change" }));
    await waitFor(() => expect(api.verifies).toHaveLength(1));
    expect(api.verifies[0]!.body.word_ids).toEqual(["p118w1a", "p118w1b", "p118w9"]);
  });

  test("a Please check fact can't be saved until words are chosen, then sends them", async () => {
    const api = mockReader(pack, { alreadyRead: true, facts: [fact(1, { status: "verified", needs_recheck: true })] });
    const { user } = renderAt(PATH);
    const c = await screen.findByRole("article", { name: /Dr P consultation/ });
    await user.click(within(c).getByRole("button", { name: "Change a detail" }));
    await waitFor(() => expect(word("visit")).toBeVisible());
    expect(word("Dr")).toHaveAttribute("aria-pressed", "false");
    const save = within(c).getByRole("button", { name: "Save change" });
    expect(save).toBeDisabled();
    expect(within(c).getByText("Choose the words on the page that show this.")).toBeVisible();
    await user.click(save);
    expect(api.verifies).toHaveLength(0);

    await user.click(word("Dr"));
    await user.click(word("visit"));
    expect(save).toBeEnabled();
    await user.click(save);
    await waitFor(() => expect(api.verifies).toHaveLength(1));
    expect(api.verifies[0]!.body).toMatchObject({ action: "edit", word_ids: ["p118w1a", "p118w9"] });
  });

  test("the server's refusal is shown in words for its code", async () => {
    const api = mockReader(pack, { alreadyRead: true });
    api.verifyStatus = 422;
    api.verifyCode = "words_needed";
    const { user } = renderAt(PATH);
    const c = await screen.findByRole("article", { name: /Dr P consultation/ });
    await user.click(within(c).getByRole("button", { name: "Change a detail" }));
    await user.click(within(c).getByRole("button", { name: "Save change" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose the words on the page that show this.");
  });
});
