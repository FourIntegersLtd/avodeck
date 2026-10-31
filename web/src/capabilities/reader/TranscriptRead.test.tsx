// Screen 9b, Reading by transcript (ADR 0028): the entries, what the browser reports as seen, the Word copy.
// Under both pack sets.
import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { fixturePack, launchPack } from "../../test/fixtures";
import { http, HttpResponse, json, server } from "../../test/api";
import { axeViolations, renderAt } from "../../test/render";
import { CASE_ID } from "./testData";
import { mockReader } from "./testing";
import "./index";

const PATH = `/cases/${CASE_ID}/read/transcript`;
const C = `/v1/cases/${CASE_ID}`;

const entry = (n: number, over: Record<string, unknown> = {}) => ({
  id: `00000000-0000-0000-0000-00000000000${n}`,
  page_no: 10 + n,
  kind: "consultation",
  kind_label: "zz.entry.session",
  folded: false,
  author_role: null,
  author_label: null,
  specialty: null,
  event_date: "2021-03-03",
  date_basis: null,
  lines: [`line ${n}`],
  results: [],
  result_labels: [],
  ...over,
});

interface Seen {
  seen: unknown[];
  downloads: number;
  retries: number;
}

function mockTranscript(
  status: "ready" | "preparing" | "failed",
  entries = [entry(1), entry(2, { folded: true })],
  picture_pages: number[] = [],
  unread_pages: number[] = [],
): Seen {
  const log: Seen = { seen: [], downloads: 0, retries: 0 };
  server.use(
    http.get(`${C}/transcript/entries`, () => json({ status, total: entries.length, entries, picture_pages, unread_pages })),
    http.post(`${C}/transcript/retry`, () => {
      log.retries += 1;
      return json({ status: "preparing" }, 202);
    }),
    http.post(`${C}/transcript/seen`, async ({ request }) => {
      log.seen.push(await request.json());
      return json({ pages_read: [] });
    }),
    http.post(`${C}/transcript/download`, () => {
      log.downloads += 1;
      return json({ job_id: "00000000-0000-0000-0000-0000000000aa" }, 202);
    }),
    http.get(`${C}/transcript/download/:job`, () => new HttpResponse(new Blob(["docx"]), { status: 200 })),
  );
  return log;
}

type Watcher = { el: Element; fire: (hit: Partial<IntersectionObserverEntry>) => void };
let watchers: Watcher[] = [];

class FakeObserver {
  constructor(private cb: IntersectionObserverCallback) {}
  observe(el: Element) {
    watchers.push({ el, fire: (hit) => this.cb([{ target: el, ...hit } as IntersectionObserverEntry], this as unknown as IntersectionObserver) });
  }
  disconnect() {}
  unobserve() {}
  takeRecords() {
    return [];
  }
}

beforeEach(() => {
  watchers = [];
  vi.stubGlobal("IntersectionObserver", FakeObserver);
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:docx"), revokeObjectURL: vi.fn() }));
});

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("Reading by transcript under the $name", ({ pack }: { pack: Schemas["CasePack"] }) => {
  test("lists the entries with a way back to page by page, and has no violations", async () => {
    mockReader(pack);
    mockTranscript("ready");
    const { container } = renderAt(PATH);
    expect(await screen.findByRole("heading", { level: 1, name: "Read by transcript" })).toBeVisible();
    expect(await screen.findByLabelText("Entry on page 11")).toBeVisible();
    expect(screen.getByText("line 1")).toBeVisible();
    expect(screen.getByRole("link", { name: "Read page by page instead" })).toHaveAttribute("href", `/cases/${CASE_ID}/read`);
    expect(screen.getAllByRole("link", { name: "See the page" })[0]).toHaveAttribute("href", `/cases/${CASE_ID}/read/11`);
    expect(await axeViolations(container)).toEqual([]);
  });

  test("says the record is being prepared and offers no download yet", async () => {
    mockReader(pack);
    mockTranscript("preparing", []);
    renderAt(PATH);
    expect(await screen.findByText("We're getting the record ready to read. This can take a minute.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Download as Word" })).toBeDisabled();
  });

  test("offers to try again when the record could not be prepared", async () => {
    mockReader(pack);
    mockTranscript("failed", []);
    renderAt(PATH);
    expect(await screen.findByText("We couldn't get your transcript ready.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Try again" })).toBeVisible();
  });

  test("Try again asks the server to prepare it again, once per press", async () => {
    mockReader(pack);
    const log = mockTranscript("failed", []);
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Try again" }));
    await waitFor(() => expect(log.retries).toBe(1));
  });

  test("a page the computer could not read says so and offers its picture", async () => {
    mockReader(pack);
    mockTranscript("ready", [entry(1)], [], [7]);
    renderAt(PATH);
    expect(await screen.findByText("This page couldn't be read by the computer; look at the picture.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Open the picture of page 7" })).toBeVisible();
  });

  test("the Word button asks for the file and saves it", async () => {
    mockReader(pack);
    const log = mockTranscript("ready");
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Download as Word" }));
    expect(await screen.findByText("Your Word file has been downloaded.")).toBeVisible();
    expect(log.downloads).toBe(1);
  });

  test("the reading screen links to the transcript", async () => {
    mockReader(pack);
    renderAt(`/cases/${CASE_ID}/read`);
    await waitFor(() =>
      expect(screen.getByRole("link", { name: "Read by transcript" })).toHaveAttribute("href", `/cases/${CASE_ID}/read/transcript`),
    );
  });

  test("a tall entry that fills the screen counts as seen though little of it is in view by ratio", async () => {
    mockReader(pack);
    const log = mockTranscript("ready", [entry(1)]);
    renderAt(PATH);
    await screen.findByLabelText("Entry on page 11");
    const now = vi.spyOn(Date, "now");
    now.mockReturnValue(1000);
    watchers[0]?.fire({ isIntersecting: true, intersectionRatio: 0.2, intersectionRect: { height: 700 } as DOMRectReadOnly, rootBounds: { height: 800 } as DOMRectReadOnly });
    now.mockReturnValue(3000);
    watchers[0]?.fire({ isIntersecting: false, intersectionRatio: 0 });
    await waitFor(() => expect(log.seen).toEqual([{ entry_id: "00000000-0000-0000-0000-000000000001", dwell_ms: 2000, picture_opened: false }]));
    now.mockRestore();
  });

  test("an entry only a little in view is not reported", async () => {
    mockReader(pack);
    const log = mockTranscript("ready", [entry(1)]);
    renderAt(PATH);
    await screen.findByLabelText("Entry on page 11");
    const now = vi.spyOn(Date, "now");
    now.mockReturnValue(1000);
    watchers[0]?.fire({ isIntersecting: true, intersectionRatio: 0.2, intersectionRect: { height: 100 } as DOMRectReadOnly, rootBounds: { height: 800 } as DOMRectReadOnly });
    now.mockReturnValue(5000);
    watchers[0]?.fire({ isIntersecting: false, intersectionRatio: 0 });
    expect(log.seen).toEqual([]);
    now.mockRestore();
  });

  test("a folded entry is not watched until it is unfolded", async () => {
    mockReader(pack);
    mockTranscript("ready", [entry(2, { folded: true })]);
    const { user } = renderAt(PATH);
    await screen.findByLabelText("Entry on page 12");
    expect(watchers).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Show this entry" }));
    expect(watchers).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Show this entry" })).toBeNull();
  });

  test("opening the picture of a page with no entries reports it", async () => {
    mockReader(pack);
    const log = mockTranscript("ready", [], [3]);
    server.use(http.get(`${C}/pages/3/image`, () => new HttpResponse(new Blob(["png"]), { status: 200 })));
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Open the picture of page 3" }));
    expect(await screen.findByAltText("Picture of page 3")).toBeVisible();
    expect(log.seen).toEqual([{ page_no: 3, dwell_ms: 0, picture_opened: true }]);
  });

  test("a result chip uses the pack's label key and falls back to plain words", async () => {
    mockReader(pack);
    mockTranscript("ready", [entry(1, { results: ["zz_level"], result_labels: ["zz.result.level"] })]);
    renderAt(PATH);
    expect(await screen.findByText("Zz level")).toBeVisible();
  });
});
