// Screen 08 while "Try these pages again" runs: a real progress bar from the server's own counts (percent, pages
// done of pages retried, pages that failed again), checked every 3 seconds, and a plain result line at the end.
// Runs under both pack sets.
import { act, screen, waitFor } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { http, json, server, signedIn } from "../../test/api";
import { FakeEventSource, liveJob } from "../../test/eventSource";
import { fixturePack, launchPack } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { CASE_ID, missingList, summary, theCase } from "./testData";

const PATH = `/cases/${CASE_ID}/bundle`;
const BUTTON = "Try these pages again";

type Summary = Partial<Schemas["BundleSummary"]>;
type Retry = NonNullable<Schemas["BundleSummary"]["retry"]>;

function mock(pack: Schemas["CasePack"], answers: Summary[]) {
  signedIn({ pack });
  const log = { summaries: 0 };
  server.use(
    http.get(`/v1/cases/${CASE_ID}`, () => json(theCase())),
    http.get(`/v1/cases/${CASE_ID}/bundle/summary`, () => {
      const over = answers[Math.min(log.summaries, answers.length - 1)];
      log.summaries += 1;
      return json(summary(pack, over));
    }),
    http.get(`/v1/cases/${CASE_ID}/missing-items`, () => json(missingList([]))),
  );
  return log;
}

const running = (done: number, failed: number, total = 650): Summary => ({
  failed_pages: total - done,
  retrying: true,
  retry: { running: true, paused: false, total, done, failed, allowance_used_up: false } satisfies Retry,
});

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("Retry progress on the bundle screen under the $name", ({ pack }) => {
  test("shows a progress bar with the percent, the pages done of the pages retried, and the pages that failed again", async () => {
    mock(pack, [running(117, 3)]);
    const { container } = renderAt(PATH);
    const bar = await screen.findByRole("progressbar", { name: "Trying again: 120 of 650 pages (18%)" });
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuenow", "120");
    expect(bar).toHaveAttribute("aria-valuemax", "650");
    expect(screen.getAllByText("Trying again: 120 of 650 pages (18%)").length).toBeGreaterThan(0);
    expect(screen.getByText("3 pages could not be read again.")).toBeVisible();
    expect(screen.queryByRole("button", { name: BUTTON })).toBeNull();
    expect(await axeViolations(container)).toEqual([]);
  });

  test("says nothing about pages failing again while none has", async () => {
    mock(pack, [running(40, 0)]);
    renderAt(PATH);
    await screen.findByRole("progressbar", { name: "Trying again: 40 of 650 pages (6%)" });
    expect(screen.queryByText(/could not be read again/)).toBeNull();
  });

  test("a single page that failed again is said in the singular", async () => {
    mock(pack, [running(9, 1, 20)]);
    renderAt(PATH);
    await screen.findByRole("progressbar", { name: "Trying again: 10 of 20 pages (50%)" });
    expect(screen.getByText("1 page could not be read again.")).toBeVisible();
  });

  test("follows the retry job live and ends with a plain line when every page was read", async () => {
    const log = mock(pack, [
      running(117, 3),
      running(400, 3),
      { failed_pages: 0, retrying: false, retry: { running: false, paused: false, total: 650, done: 650, failed: 0, allowance_used_up: false } },
    ]);
    renderAt(PATH);
    await screen.findByRole("progressbar", { name: "Trying again: 120 of 650 pages (18%)" });
    await waitFor(() => expect(FakeEventSource.open()).toHaveLength(1));
    const retryJob = (over: Record<string, unknown>) => liveJob({ id: "r1", kind: "retry_unread_pages", ...over });
    act(() => FakeEventSource.open()[0]!.emitJobs([retryJob({ status: "running", step_done: 403, step_total: 650 })]));
    expect(await screen.findByRole("progressbar", { name: "Trying again: 403 of 650 pages (62%)" })).toBeVisible();
    act(() => FakeEventSource.open()[0]!.emitJobs([retryJob({ status: "done", step_done: 650, step_total: 650 })]));
    expect(await screen.findByText("All done. Every page was read.", {}, { timeout: 4000 })).toBeVisible();
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.queryByRole("button", { name: BUTTON })).toBeNull();
    expect(log.summaries).toBeGreaterThanOrEqual(3);
  });

  test("ends by saying how many pages still could not be read, with the button to try again", async () => {
    mock(pack, [
      {
        failed_pages: 10,
        retrying: false,
        retry: { running: false, paused: false, total: 650, done: 640, failed: 10, allowance_used_up: false },
      },
    ]);
    renderAt(PATH);
    expect(await screen.findByText("10 pages still could not be read.")).toBeVisible();
    expect(screen.getByRole("button", { name: BUTTON })).toBeVisible();
    expect(screen.queryByRole("progressbar")).toBeNull();
    await waitFor(() => expect(screen.queryByText(/All done/)).toBeNull());
  });

  test("never shows 100% while the run is still going", async () => {
    mock(pack, [running(649, 1)]);
    renderAt(PATH);
    expect(await screen.findByRole("progressbar", { name: "Trying again: 650 of 650 pages (99%)" })).toBeVisible();
  });

  test("a paused run says so in plain words, shows no moving bar, keeps the button, and is not asked about again", async () => {
    const log = mock(pack, [
      { failed_pages: 530, retrying: true, retry: { running: false, paused: true, total: 650, done: 117, failed: 3, allowance_used_up: false } },
    ]);
    renderAt(PATH);
    expect(await screen.findByText("Trying again has paused for now. Please check back in a little while.")).toBeVisible();
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.getByRole("button", { name: BUTTON })).toBeVisible(); // a pause that never ends by itself must not be a dead end
    await new Promise((resolve) => setTimeout(resolve, 3600));
    expect(log.summaries).toBe(1);
  }, 10000);

  test("with no retry run the screen is as before", async () => {
    mock(pack, [{ failed_pages: 0, retrying: false, retry: null }]);
    renderAt(PATH);
    expect(await screen.findByRole("heading", { level: 1, name: "Your bundle is ready" })).toBeVisible();
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.queryByText(/All done/)).toBeNull();
  });
});
