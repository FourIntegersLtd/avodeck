// Screen 08 when "Try these pages again" has paused: it says why in plain words (from a fixed reason code), keeps the
// button for people who may start the work, and pressing it asks the server to hand the same run back. Nothing is
// checked every few seconds while it is paused (nothing is moving). Runs under both pack sets.
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { http, json, server, signedIn } from "../../test/api";
import { fixturePack, launchPack } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { CASE_ID, missingList, summary, theCase, VIEWER_ACTIONS } from "./testData";

const PATH = `/cases/${CASE_ID}/bundle`;
const BUTTON = "Try these pages again";
const GENERIC = "Trying again has paused for now. Please check back in a little while.";
const ALLOWANCE = "This case has used up its processing allowance for now.";

type Summary = Partial<Schemas["BundleSummary"]>;

function mock(pack: Schemas["CasePack"], answers: Summary[], item: Schemas["Case"] = theCase()) {
  signedIn({ pack });
  const log = { retries: 0, summaries: 0 };
  server.use(
    http.get(`/v1/cases/${CASE_ID}`, () => json(item)),
    http.get(`/v1/cases/${CASE_ID}/bundle/summary`, () => {
      const over = answers[Math.min(log.summaries, answers.length - 1)];
      log.summaries += 1;
      return json(summary(pack, over));
    }),
    http.get(`/v1/cases/${CASE_ID}/missing-items`, () => json(missingList([]))),
    http.post(`/v1/cases/${CASE_ID}/transcript/retry`, () => {
      log.retries += 1;
      return json({ status: "preparing" }, 202);
    }),
  );
  return log;
}

const paused = (reason: "cost_guard" | "other"): Summary => ({
  failed_pages: 530,
  retrying: true,
  retry: { running: false, paused: true, pause_reason: reason, total: 650, done: 117, failed: 3 },
});
const running: Summary = {
  failed_pages: 530,
  retrying: true,
  retry: { running: true, paused: false, pause_reason: null, total: 650, done: 117, failed: 3 },
};

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("A paused retry on the bundle screen under the $name", ({ pack }) => {
  test("a pause for the processing allowance says so, and the button stays", async () => {
    mock(pack, [paused("cost_guard")]);
    renderAt(PATH);
    expect(await screen.findByText(ALLOWANCE)).toBeVisible();
    expect(screen.queryByText(GENERIC)).toBeNull();
    expect(screen.getByRole("button", { name: BUTTON })).toBeVisible();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  test("any other pause gives the general line, and the button stays", async () => {
    mock(pack, [paused("other")]);
    renderAt(PATH);
    expect(await screen.findByText(GENERIC)).toBeVisible();
    expect(screen.queryByText(ALLOWANCE)).toBeNull();
    expect(screen.getByRole("button", { name: BUTTON })).toBeVisible();
  });

  test("someone who may not start the work sees why it paused but no button", async () => {
    mock(pack, [paused("cost_guard")], theCase({ allowed_actions: VIEWER_ACTIONS }));
    renderAt(PATH);
    expect(await screen.findByText(ALLOWANCE)).toBeVisible();
    await waitFor(() => expect(screen.queryByRole("button", { name: BUTTON })).toBeNull());
  });

  test("pressing it asks the server once and shows the run moving again", async () => {
    const log = mock(pack, [paused("other"), running]);
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: BUTTON }));
    expect(await screen.findByRole("progressbar", { name: "Trying again: 120 of 650 pages (18%)" })).toBeVisible();
    expect(log.retries).toBe(1);
    expect(screen.queryByRole("button", { name: BUTTON })).toBeNull();
  });

  test("when it pauses again for the allowance the screen says so again, not as if it were broken", async () => {
    const log = mock(pack, [paused("cost_guard"), paused("cost_guard")]);
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: BUTTON }));
    await waitFor(() => expect(log.retries).toBe(1));
    expect(await screen.findByText(ALLOWANCE)).toBeVisible();
    expect(screen.getByRole("button", { name: BUTTON })).toBeVisible();
    expect(screen.queryByText("We couldn't start that just now. Please try again.")).toBeNull();
  });

  test("a paused run is not asked about again while it waits", async () => {
    const log = mock(pack, [paused("cost_guard")]);
    renderAt(PATH);
    await screen.findByText(ALLOWANCE);
    await new Promise((resolve) => setTimeout(resolve, 3600));
    expect(log.summaries).toBe(1);
  }, 10000);
});
