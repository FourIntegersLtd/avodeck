// Screen 08, Your bundle is ready, when pages could not be processed: it says so plainly at the top, keeps the
// "Not sorted yet" count, and offers one button that asks about just those pages again. Runs under both pack sets.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { http, json, server, signedIn } from "../../test/api";
import { fixturePack, launchPack } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { CASE_ID, missingList, summary, theCase, VIEWER_ACTIONS } from "./testData";

const PATH = `/cases/${CASE_ID}/bundle`;
const BUTTON = "Try these pages again";

type Summary = Partial<Schemas["BundleSummary"]>;

/** The summary answers from `answers` in turn (the last one repeats); returns how often retry was pressed. */
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

const stuck = (pack: Schemas["CasePack"]): Summary => ({
  by_source: [...summary(pack).by_source.slice(0, 1), { source_type: null, pages: 650 }],
  failed_pages: 650,
  retrying: false,
});

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("Bundle ready with pages not processed under the $name", ({ pack }) => {
  test("says plainly how many pages were not processed, instead of an unqualified ready", async () => {
    mock(pack, [stuck(pack)]);
    const { container } = renderAt(PATH);
    const warning = (await screen.findByText("650 pages weren't processed.")).closest(".av-notice") as HTMLElement;
    expect(warning).toBeVisible();
    expect(warning).toHaveTextContent("The computer couldn't make sense of them. This is not a problem with your files.");
    expect(screen.queryByRole("heading", { level: 1, name: "Your bundle is ready" })).toBeNull();
    expect(screen.getByRole("heading", { level: 1, name: "Your bundle is partly ready" })).toBeVisible();
    expect(await axeViolations(container)).toEqual([]);
  });

  test("keeps the Not sorted yet count", async () => {
    mock(pack, [stuck(pack)]);
    renderAt(PATH);
    await screen.findByText("650 pages weren't processed.");
    const list = screen.getByRole("list", { name: "Where the pages came from" });
    const row = within(list).getAllByRole("listitem").find((li) => li.textContent?.includes("Not sorted yet"));
    expect(row).toHaveTextContent("650");
  });

  test("the main button asks again once, shows it is trying, and the page refreshes when it is done", async () => {
    const log = mock(pack, [stuck(pack), { ...stuck(pack), retrying: true }, { failed_pages: 0, retrying: false }]);
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: BUTTON }));
    expect(log.retries).toBe(1);
    expect(await screen.findByText("Trying those pages again. This can take a few minutes.")).toBeVisible();
    expect(screen.queryByRole("button", { name: BUTTON })).toBeNull();
    // It checks by itself while the job runs, and shows the ordinary ready page when nothing is left.
    expect(await screen.findByRole("heading", { level: 1, name: "Your bundle is ready" }, { timeout: 8000 })).toBeVisible();
    expect(screen.queryByText("650 pages weren't processed.")).toBeNull();
    expect(log.retries).toBe(1);
  }, 15000);

  test("a retry already under way shows the trying state with no button", async () => {
    const log = mock(pack, [{ ...stuck(pack), retrying: true }]);
    renderAt(PATH);
    expect(await screen.findByText("Trying those pages again. This can take a few minutes.")).toBeVisible();
    expect(screen.queryByRole("button", { name: BUTTON })).toBeNull();
    expect(log.retries).toBe(0);
  });

  test("with no failed pages there is no warning and no button", async () => {
    mock(pack, [{ failed_pages: 0, retrying: false }]);
    renderAt(PATH);
    expect(await screen.findByRole("heading", { level: 1, name: "Your bundle is ready" })).toBeVisible();
    expect(screen.queryByText(/weren't processed/)).toBeNull();
    expect(screen.queryByRole("button", { name: BUTTON })).toBeNull();
  });

  test("someone who may not start the work sees the warning but no button", async () => {
    mock(pack, [stuck(pack)], theCase({ allowed_actions: VIEWER_ACTIONS }));
    renderAt(PATH);
    expect(await screen.findByText("650 pages weren't processed.")).toBeVisible();
    await waitFor(() => expect(screen.queryByRole("button", { name: BUTTON })).toBeNull());
  });

  test("a failed press says so and leaves the button to press again", async () => {
    mock(pack, [stuck(pack)]);
    server.use(http.post(`/v1/cases/${CASE_ID}/transcript/retry`, () => json({ detail: "x" }, 500)));
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: BUTTON }));
    expect(await screen.findByText("We couldn't start that just now. Please try again.")).toBeVisible();
    expect(screen.getByRole("button", { name: BUTTON })).toBeVisible();
  });
});
