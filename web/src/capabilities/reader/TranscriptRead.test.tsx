// Screen 9b, Reading by transcript (ADR 0028): the entries, what the browser reports as seen, the Word copy.
// Under both pack sets.
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { forgetPages } from "../../foundation/lib/pageImageCache";
import type { Schemas } from "../../foundation/platform-client";
import { fixturePack, launchPack } from "../../test/fixtures";
import { http, HttpResponse, json, server } from "../../test/api";
import { axeViolations, renderAt } from "../../test/render";
import { CASE_ID } from "./testData";
import { mockReader } from "./testing";
import "./index";

const PATH = `/cases/${CASE_ID}/read/transcript`;
const SECTION = `${PATH}?section=alpha`;
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
  /** Every entries request, as its offset and limit. */
  requests: Array<{ offset: number; limit: number }>;
  seen: unknown[];
  downloads: number;
  bodies: unknown[];
  outlines: number;
  retries: number;
}

type Entries = ReturnType<typeof entry>[];
type OutlineSection = Schemas["OutlineSection"];

/** The years of a section's entries, as the server counts them: runs of the same year, no date last. */
function yearsOf(entries: Entries, offset = 0): OutlineSection["years"] {
  const years: OutlineSection["years"] = [];
  entries.forEach((e, i) => {
    const year = e.event_date ? Number(String(e.event_date).slice(0, 4)) : null;
    const last = years[years.length - 1];
    if (last && last.year === year) last.count += 1;
    else years.push({ year, offset: offset + i, count: 1 });
  });
  return years;
}

function sectionOf(key: string, entries: Entries, offset = 0): OutlineSection {
  const dates = entries.map((e) => e.event_date).filter(Boolean) as string[];
  return {
    key,
    label: `zz.section.${key}`,
    count: entries.length,
    offset,
    pages: new Set(entries.map((e) => e.page_no)).size,
    first_date: dates.length ? dates.reduce((a, b) => (a < b ? a : b)) : null,
    last_date: dates.length ? dates.reduce((a, b) => (a > b ? a : b)) : null,
    years: yearsOf(entries, offset),
  };
}

const COVERAGE = { total_pages: 20, with_entries: 12, no_entries: 5, unreadable: 3, set_aside: 2 };

let outline: Schemas["TranscriptOutline"] = { status: "ready", sections: [] };
// What the bundle summary says about the pages the model could not read (the same count the retry job uses).
let summary: { failed_pages: number; retrying: boolean; retry?: { running: boolean; paused: boolean; total: number; done: number; failed: number } } = {
  failed_pages: 0,
  retrying: false,
};

function mockTranscript(
  status: "ready" | "preparing" | "failed" | "idle",
  entries = [entry(1), entry(2, { folded: true })],
  picture_pages: number[] = [],
  unread_pages: number[] = [],
  extra: Record<string, unknown> = {},
): Seen {
  const log: Seen = { requests: [], seen: [], downloads: 0, bodies: [], outlines: 0, retries: 0 };
  // Most tests below open section "alpha"; unless a test set its own outline, it holds all of these entries.
  if (outline.sections.length === 0 && entries.length > 0) outline = { status: "ready", coverage: COVERAGE, sections: [sectionOf("alpha", entries)] };
  server.use(
    http.get(`${C}/transcript/entries`, ({ request }) => {
      const query = new URL(request.url).searchParams;
      const offset = Number(query.get("offset") ?? 0);
      const limit = Number(query.get("limit") ?? 50);
      log.requests.push({ offset, limit });
      return json({ status, total: entries.length, entries: entries.slice(offset, offset + limit), picture_pages, unread_pages, ...extra });
    }),
    http.get(`${C}/bundle/summary`, () => json(summary)),
    http.post(`${C}/transcript/retry`, () => {
      log.retries += 1;
      return json({ status: "preparing" }, 202);
    }),
    http.post(`${C}/transcript/seen`, async ({ request }) => {
      log.seen.push(await request.json());
      return json({ pages_read: [] });
    }),
    http.get(`${C}/transcript/outline`, () => {
      log.outlines += 1;
      return json(outline);
    }),
    http.post(`${C}/transcript/download`, async ({ request }) => {
      log.downloads += 1;
      log.bodies.push(await request.json());
      return json({ job_id: "00000000-0000-0000-0000-0000000000aa" }, 202);
    }),
    http.get(`${C}/transcript/download/:job`, () => new HttpResponse(new Blob(["docx"]), { status: 200 })),
  );
  return log;
}

const WAIT_WORDS = "We're getting the record ready to read. This can take a minute.";
const waitNoticeNow = () => screen.getByText(WAIT_WORDS).closest("[role=status]") as HTMLElement;
const waitNotice = async () => {
  await screen.findByText(WAIT_WORDS);
  return waitNoticeNow();
};

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
  forgetPages(); // a page picture kept by an earlier test would hide the request this one asserts on
  watchers = [];
  outline = { status: "ready", sections: [] };
  summary = { failed_pages: 0, retrying: false };
  vi.stubGlobal("IntersectionObserver", FakeObserver);
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:docx"), revokeObjectURL: vi.fn() }));
});

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("Reading by transcript under the $name", ({ pack }: { pack: Schemas["CasePack"] }) => {
  test("lists the entries with a way back to page by page, and has no violations", async () => {
    mockReader(pack);
    mockTranscript("ready");
    const { container } = renderAt(SECTION);
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
    const { user } = renderAt(PATH);
    expect(await screen.findByText("We're getting the record ready to read. This can take a minute.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Download a copy" }));
    expect(screen.getByRole("menuitem", { name: "Download as Word" })).toHaveAttribute("aria-disabled", "true");
  });

  test("a transcript with pages that can be tried again offers to try just those, once per press", async () => {
    mockReader(pack);
    summary = { failed_pages: 3, retrying: false };
    const log = mockTranscript("ready", [entry(1)], [], [7, 8, 9], { can_prepare: true });
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Try these pages again" }));
    expect(log.retries).toBe(1);
  });

  test("the button follows the pages the retry would try, not the pages listed as unreadable", async () => {
    mockReader(pack);
    summary = { failed_pages: 0, retrying: false }; // the unreadable pages are copies or set aside: a press would queue nothing
    mockTranscript("ready", [entry(1)], [], [7, 8, 9], { can_prepare: true });
    renderAt(PATH);
    await screen.findByText("Pages we couldn't read");
    expect(screen.queryByRole("button", { name: "Try these pages again" })).toBeNull();
  });

  test("no button to try those pages again for a person who may not start the work", async () => {
    mockReader(pack);
    summary = { failed_pages: 3, retrying: false };
    mockTranscript("ready", [entry(1)], [], [7, 8, 9], { can_prepare: false });
    renderAt(PATH);
    await screen.findByText("Pages we couldn't read");
    expect(screen.queryByRole("button", { name: "Try these pages again" })).toBeNull();
  });

  test("while those pages are tried again it says so, rather than looking like a fresh start", async () => {
    mockReader(pack);
    summary = { failed_pages: 3, retrying: true };
    mockTranscript("preparing", [], [], [], { prepared_pages: 20, total_pages: 900, extracted_pages: 897, waiting_for: "entries" });
    renderAt(PATH);
    expect(await screen.findByText("We're trying the pages we couldn't read again. This can take a minute.")).toBeVisible();
    expect(screen.queryByText(WAIT_WORDS)).toBeNull();
  });

  test("while those pages are tried again it shows how far the retry has got, from the server's own counts", async () => {
    mockReader(pack);
    summary = { failed_pages: 530, retrying: true, retry: { running: true, paused: false, total: 650, done: 117, failed: 3 } };
    mockTranscript("preparing", [], [], [], { prepared_pages: 20, total_pages: 900, extracted_pages: 897, waiting_for: "entries" });
    renderAt(PATH);
    const bar = await screen.findByRole("progressbar", { name: "Trying again: 120 of 650 pages (18%)" });
    expect(bar).toHaveAttribute("aria-valuenow", "120");
    expect(bar).toHaveAttribute("aria-valuemax", "650");
    expect(screen.queryByRole("progressbar", { name: "Preparing page 20 of 900" })).toBeNull();
  });

  test("the retry's bar moves on as the summary is asked again", async () => {
    mockReader(pack);
    summary = { failed_pages: 530, retrying: true, retry: { running: true, paused: false, total: 650, done: 117, failed: 3 } };
    mockTranscript("preparing", [], [], [], { prepared_pages: 20, total_pages: 900, extracted_pages: 897, waiting_for: "entries" });
    renderAt(PATH);
    await screen.findByRole("progressbar", { name: "Trying again: 120 of 650 pages (18%)" });
    summary = { failed_pages: 250, retrying: true, retry: { running: true, paused: false, total: 650, done: 397, failed: 3 } };
    expect(await screen.findByRole("progressbar", { name: "Trying again: 400 of 650 pages (62%)" }, { timeout: 6000 })).toBeVisible();
  }, 15000);

  test("the bar shows on the retry's own running flag, even when the summary's retrying flag is off", async () => {
    mockReader(pack);
    summary = { failed_pages: 530, retrying: false, retry: { running: true, paused: false, total: 650, done: 117, failed: 3 } };
    mockTranscript("preparing", [], [], [], { prepared_pages: 20, total_pages: 900, extracted_pages: 897, waiting_for: "entries" });
    renderAt(PATH);
    expect(await screen.findByRole("progressbar", { name: "Trying again: 120 of 650 pages (18%)" })).toBeVisible();
  });

  test("the retry's bar never shows 100% while the run is still going", async () => {
    mockReader(pack);
    summary = { failed_pages: 1, retrying: true, retry: { running: true, paused: false, total: 650, done: 649, failed: 1 } };
    mockTranscript("preparing", [], [], [], { prepared_pages: 20, total_pages: 900, extracted_pages: 897, waiting_for: "entries" });
    renderAt(PATH);
    expect(await screen.findByRole("progressbar", { name: "Trying again: 650 of 650 pages (99%)" })).toBeVisible();
  });

  test("a paused retry says so in plain words instead of showing a bar", async () => {
    mockReader(pack);
    summary = { failed_pages: 530, retrying: true, retry: { running: false, paused: true, total: 650, done: 117, failed: 3 } };
    mockTranscript("preparing", [], [], [], { prepared_pages: 20, total_pages: 900, extracted_pages: 897, waiting_for: "entries" });
    renderAt(PATH);
    expect(await screen.findByText("Trying again has paused for now. Please check back in a little while.")).toBeVisible();
    expect(screen.queryByRole("progressbar", { name: /Trying again/ })).toBeNull();
  });

  test("while preparing it shows a spinner and how many pages are done", async () => {
    mockReader(pack);
    mockTranscript("preparing", [], [], [], { prepared_pages: 20, total_pages: 900, extracted_pages: 900, waiting_for: "entries" });
    renderAt(PATH);
    const status = await waitNotice();
    expect(status).toHaveTextContent("Preparing page 20 of 900");
    expect(screen.getByRole("progressbar", { name: "Preparing page 20 of 900" })).toHaveAttribute("aria-valuenow", "20");
    expect(status.querySelector(".av-spinner")).not.toBeNull();
  });

  async function slowWait(status: "preparing" | "idle") {
    mockTranscript(status, [], [], [], { prepared_pages: 0, total_pages: 0, waiting_for: "entries", can_prepare: true });
    renderAt(PATH);
    await waitNotice();
    await act(async () => {
      vi.setSystemTime(Date.now() + 79_000);
      vi.advanceTimersByTime(1000);
    });
    expect(waitNoticeNow()).toHaveTextContent("Working for 1 min 20 s");
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    await act(async () => {
      vi.setSystemTime(Date.now() + 120_000);
      vi.advanceTimersByTime(1000);
    });
    expect(waitNoticeNow()).toHaveTextContent("This is taking longer than usual.");
  }

  test("while the work is queued, slow means a link to see how far it got, not a Try again that does nothing", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    try {
      mockReader(pack);
      await slowWait("preparing");
      expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
      expect(screen.getByRole("link", { name: "See how far it got" })).toHaveAttribute("href", `/cases/${CASE_ID}/preparing`);
    } finally {
      vi.useRealTimers();
    }
  });

  test("when nothing is queued, slow offers Try again", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    try {
      mockReader(pack);
      await slowWait("idle");
      expect(screen.getByRole("button", { name: "Try again" })).toBeVisible();
    } finally {
      vi.useRealTimers();
    }
  });

  test("Try again is not offered to someone who cannot start the work", async () => {
    mockReader(pack);
    mockTranscript("failed", [], [], [], { can_prepare: false, waiting_for: "entries" });
    renderAt(PATH);
    expect(await screen.findByText("We couldn't get your transcript ready.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  test("pages still waiting to be read by Avodeck, with the pipeline running: spinner and a count", async () => {
    mockReader(pack);
    mockTranscript("idle", [], [], [], { extracted_pages: 140, total_pages: 900, waiting_for: "pipeline", pipeline_running: true });
    renderAt(PATH);
    const status = await waitNotice();
    expect(status).toHaveTextContent("Avodeck has read 140 of 900 pages");
    expect(status.querySelector(".av-spinner")).not.toBeNull();
  });

  test("pages waiting and nothing running: says so plainly, links on, and shows no spinner", async () => {
    mockReader(pack);
    mockTranscript("idle", [], [], [], { extracted_pages: 140, total_pages: 900, waiting_for: "pipeline", pipeline_running: false });
    renderAt(PATH);
    expect(await screen.findByText("Some pages haven't been read by Avodeck yet (760 of 900).")).toBeVisible();
    expect(screen.getByRole("link", { name: "See how far it got" })).toHaveAttribute("href", `/cases/${CASE_ID}/preparing`);
    expect(document.querySelector(".av-spinner")).toBeNull();
  });

  test("someone who cannot start the work is told to ask the named expert, with no spinner", async () => {
    mockReader(pack);
    mockTranscript("idle", [], [], [], { can_prepare: false, waiting_for: "entries" });
    renderAt(PATH);
    expect(await screen.findByText("This record has not been prepared yet. Ask the named expert to open it, and it will be ready to read.")).toBeVisible();
    expect(document.querySelector(".av-spinner")).toBeNull();
  });

  test("the intro line is shown once", async () => {
    mockReader(pack);
    mockTranscript("ready");
    renderAt(SECTION);
    await screen.findByLabelText("Entry on page 11");
    expect(screen.getAllByText(/Every entry in date order/)).toHaveLength(1);
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
    renderAt(`${PATH}?section=_unread`);
    expect(await screen.findByText("This page couldn't be read by the computer; look at the picture.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Open the picture of page 7" })).toBeVisible();
  });

  test("the Word button asks for the file and saves it", async () => {
    mockReader(pack);
    const log = mockTranscript("ready");
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Download a copy" }));
    await user.click(await screen.findByRole("menuitem", { name: "Download as Word" }));
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
    renderAt(SECTION);
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
    renderAt(SECTION);
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
    const { user } = renderAt(SECTION);
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
    const { user } = renderAt(`${PATH}?section=_no_entries`);
    await user.click(await screen.findByRole("button", { name: "Open the picture of page 3" }));
    expect(await screen.findByAltText("Picture of page 3")).toBeVisible();
    expect(log.seen).toEqual([{ page_no: 3, dwell_ms: 0, picture_opened: true }]);
  });

  test("the picture of a page the computer could not read is the full picture, not the small one, and links to the page", async () => {
    mockReader(pack);
    mockTranscript("ready", [], [], [71]);
    const asked: string[] = [];
    server.use(
      http.get(`${C}/pages/71/image`, ({ request }) => {
        asked.push(new URL(request.url).search);
        return new HttpResponse(new Blob(["png"]), { status: 200 });
      }),
    );
    const { user } = renderAt(`${PATH}?section=_unread`);
    await user.click(await screen.findByRole("button", { name: "Open the picture of page 71" }));
    expect(await screen.findByAltText("Picture of page 71")).toBeVisible();
    expect(asked).toEqual([""]);
    expect(screen.getByRole("link", { name: "See the page" })).toHaveAttribute("href", expect.stringContaining("/read/71"));
  });

  test("a result chip uses the pack's label key and falls back to plain words", async () => {
    mockReader(pack);
    mockTranscript("ready", [entry(1, { results: ["zz_level"], result_labels: ["zz.result.level"] })]);
    renderAt(SECTION);
    expect(await screen.findByText("Zz level")).toBeVisible();
  });

  test("two download buttons: Word posts docx, PDF posts pdf and saves a pdf", async () => {
    mockReader(pack);
    const log = mockTranscript("ready");
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Download a copy" }));
    await user.click(await screen.findByRole("menuitem", { name: "Download as Word" }));
    expect(await screen.findByText("Your Word file has been downloaded.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Download a copy" }));
    await user.click(screen.getByRole("menuitem", { name: "Download as PDF" }));
    expect(await screen.findByText("Your PDF has been downloaded.")).toBeVisible();
    expect(log.bodies).toEqual([{ format: "docx" }, { format: "pdf" }]);
    await user.click(screen.getByRole("button", { name: "Download a copy" }));
    expect(screen.getByText("The file is a copy. Downloading it doesn't count as reading.")).toBeVisible();
  });

  test("both buttons are disabled while one file is being prepared", async () => {
    mockReader(pack);
    mockTranscript("ready");
    server.use(http.get(`${C}/transcript/download/:job`, () => new Promise<Response>(() => undefined)));
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Download a copy" }));
    await user.click(await screen.findByRole("menuitem", { name: "Download as PDF" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Download a copy" })).toBeVisible());
    await user.click(screen.getByRole("button", { name: "Download a copy" }));
    const busy = await screen.findAllByRole("menuitem", { name: "Preparing your file" });
    expect(busy).toHaveLength(2);
    busy.forEach((item) => expect(item).toHaveAttribute("aria-disabled", "true"));
  });

  test("the buttons are enabled when the transcript is ready even if no page has been read", async () => {
    mockReader(pack);
    server.use(http.get(`${C}/progress`, () => json({ read: 0, total: 40 })));
    mockTranscript("ready");
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Download a copy" }));
    expect(await screen.findByRole("menuitem", { name: "Download as Word" })).toBeEnabled();
    expect(screen.getByRole("menuitem", { name: "Download as PDF" })).toBeEnabled();
  });

  describe("sections", () => {
    const alpha = [entry(1, { event_date: "2019-03-01" }), entry(2, { event_date: "2019-07-02" }), entry(3, { event_date: "2021-01-05" }), entry(4, { event_date: null })];
    const beta = [entry(5, { event_date: "2022-06-01" })];
    const sectioned = () => [...alpha, ...beta];
    const withOutline = () => {
      outline = { status: "ready", coverage: COVERAGE, sections: [sectionOf("alpha", alpha), sectionOf("beta", beta, alpha.length)] };
    };

    test("the opening view shows the four numbers and a contents row per section, never an entry or a read", async () => {
      mockReader(pack);
      withOutline();
      const log = mockTranscript("ready", sectioned(), [3], [7]);
      const { container } = renderAt(PATH);
      expect(await screen.findByRole("heading", { level: 2, name: "About this transcript" })).toBeVisible();
      const tile = (label: string) => screen.getByText(label, { selector: "dt" }).closest("div") as HTMLElement;
      expect(tile("Pages in the bundle")).toHaveTextContent("20");
      expect(tile("Pages with entries")).toHaveTextContent("12");
      expect(tile("Pages with no entries")).toHaveTextContent("5");
      expect(tile("Pages we couldn't read")).toHaveTextContent("3");
      expect(screen.getByText("2 pages set aside (copies, replaced or held back) are not included.")).toBeVisible();
      expect(screen.getByRole("heading", { level: 2, name: "Contents" })).toBeVisible();
      const alphaRow = screen.getByRole("link", { name: /Alpha/ });
      expect(alphaRow).toHaveTextContent("4 entries");
      expect(alphaRow).toHaveTextContent("Mar 2019 – Jan 2021");
      expect(screen.getByRole("link", { name: /Beta/ })).toHaveTextContent("1 entry");
      expect(screen.queryByLabelText(/Entry on page/)).toBeNull();
      expect(screen.queryByText("line 1")).toBeNull();
      expect(screen.queryByText("Jump to")).toBeNull();
      expect(log.seen).toEqual([]);
      expect(log.requests.every((r) => r.limit === 1)).toBe(true);
      expect(await axeViolations(container)).toEqual([]);
    });

    test("a contents row opens its section from the section's own offset and puts it in the address", async () => {
      mockReader(pack);
      withOutline();
      const log = mockTranscript("ready", sectioned());
      const { user, router, container } = renderAt(PATH);
      await user.click(await screen.findByRole("link", { name: /Beta/ }));
      expect(await screen.findByLabelText("Entry on page 15")).toBeVisible();
      expect(router.state.location.search).toBe("?section=beta");
      expect(log.requests).toContainEqual({ offset: 4, limit: 1 });
      expect(screen.getByRole("heading", { level: 2, name: "2. Beta" })).toBeVisible();
      expect(screen.queryByLabelText("Entry on page 11")).toBeNull();
      expect(log.seen).toEqual([]);
      expect(await axeViolations(container)).toEqual([]);
    });

    test("a section shows its figures, then years and months as headings, and no date last", async () => {
      mockReader(pack);
      withOutline();
      mockTranscript("ready", sectioned());
      renderAt(`${PATH}?section=alpha`);
      expect(await screen.findByRole("heading", { level: 2, name: "1. Alpha" })).toBeVisible();
      expect(screen.getByText(/4 entries · 4 pages · Mar 2019 – Jan 2021/)).toBeVisible();
      expect(screen.getByRole("link", { name: /Contents/ })).toHaveAttribute("href", expect.stringContaining(`/read/transcript`));
      const years = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
      expect(years).toEqual(["2019", "2021", "No date"]);
      expect(screen.getAllByRole("heading", { level: 4 }).map((h) => h.textContent)).toEqual(expect.arrayContaining(["March", "July", "January"]));
    });

    test("Show the next 50 asks for the next entries of this section from where it left off", async () => {
      mockReader(pack);
      const many = Array.from({ length: 120 }, (_, i) => entry(i + 1, { id: `00000000-0000-0000-0000-${String(i + 1).padStart(12, "0")}`, page_no: 100 + i, event_date: "2020-01-02" }));
      outline = { status: "ready", coverage: COVERAGE, sections: [sectionOf("alpha", many as Entries)] };
      const log = mockTranscript("ready", many as Entries);
      const { user } = renderAt(SECTION);
      await screen.findByLabelText("Entry on page 100");
      expect(log.requests).toContainEqual({ offset: 0, limit: 50 });
      await user.click(screen.getByRole("button", { name: "Show the next 50 (51–100 of 120)" }));
      expect(await screen.findByLabelText("Entry on page 150")).toBeVisible();
      expect(log.requests).toContainEqual({ offset: 50, limit: 50 });
      await user.click(screen.getByRole("button", { name: "Show the next 20 (101–120 of 120)" }));
      expect(await screen.findByLabelText("Entry on page 219")).toBeVisible();
      expect(log.requests).toContainEqual({ offset: 100, limit: 20 });
      expect(screen.queryByRole("button", { name: /Show the next/ })).toBeNull();
    });

    const manyEntries = (count: number) =>
      Array.from({ length: count }, (_, i) => entry(i + 1, { id: `00000000-0000-0000-0000-${String(i + 1).padStart(12, "0")}`, page_no: 100 + i, event_date: "2020-01-02" })) as Entries;

    test("two quick presses of Show the next 50 ask once and add each entry once", async () => {
      mockReader(pack);
      const many = manyEntries(120);
      outline = { status: "ready", coverage: COVERAGE, sections: [sectionOf("alpha", many)] };
      const log = mockTranscript("ready", many);
      renderAt(SECTION);
      await screen.findByLabelText("Entry on page 100");
      const button = screen.getByRole("button", { name: "Show the next 50 (51–100 of 120)" });
      fireEvent.click(button);
      fireEvent.click(button);
      expect(await screen.findByLabelText("Entry on page 149")).toBeVisible();
      await new Promise((r) => setTimeout(r, 50));
      expect(log.requests.filter((r) => r.offset === 50)).toHaveLength(1);
      expect(log.requests.filter((r) => r.offset === 100)).toHaveLength(0);
      expect(screen.getAllByLabelText("Entry on page 149")).toHaveLength(1);
      expect(screen.getAllByRole("article")).toHaveLength(100);
    });

    test("a failed Show the next 50 says so and offers Try again, which loads the same entries", async () => {
      mockReader(pack);
      const many = manyEntries(120);
      outline = { status: "ready", coverage: COVERAGE, sections: [sectionOf("alpha", many)] };
      mockTranscript("ready", many);
      let down = true;
      server.use(
        http.get(`${C}/transcript/entries`, ({ request }) => {
          const offset = Number(new URL(request.url).searchParams.get("offset") ?? 0);
          if (offset === 50 && down) return HttpResponse.json({ detail: "x" }, { status: 500 });
          return undefined;
        }),
      );
      const { user } = renderAt(SECTION);
      await screen.findByLabelText("Entry on page 100");
      await user.click(screen.getByRole("button", { name: "Show the next 50 (51–100 of 120)" }));
      const retry = await screen.findByRole("button", { name: "Try again" });
      expect(screen.getByLabelText("Entry on page 100")).toBeVisible();
      down = false;
      await user.click(retry);
      expect(await screen.findByLabelText("Entry on page 149")).toBeVisible();
      expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    });

    test("a failed jump to a year says so and offers Try again", async () => {
      mockReader(pack);
      const early = manyEntries(60);
      const late = [entry(70, { page_no: 300, event_date: "2023-02-02" })];
      const all = [...early, ...late] as Entries;
      outline = { status: "ready", coverage: COVERAGE, sections: [sectionOf("alpha", all)] };
      mockTranscript("ready", all);
      let down = true;
      server.use(
        http.get(`${C}/transcript/entries`, ({ request }) => {
          const offset = Number(new URL(request.url).searchParams.get("offset") ?? 0);
          if (offset === 50 && down) return HttpResponse.json({ detail: "x" }, { status: 500 });
          return undefined;
        }),
      );
      const { user } = renderAt(SECTION);
      await screen.findByLabelText("Entry on page 100");
      await user.click(within(screen.getByRole("group", { name: "Jump to a year" })).getByRole("button", { name: "2023" }));
      const retry = await screen.findByRole("button", { name: "Try again" });
      down = false;
      await user.click(retry);
      expect(await screen.findByLabelText("Entry on page 300")).toBeVisible();
    });

    test("a year jump still loading when another section is opened does not scroll the new section", async () => {
      mockReader(pack);
      const early = manyEntries(60);
      const late = [entry(70, { page_no: 300, event_date: "2023-02-02" })];
      const alpha = [...early, ...late] as Entries;
      const beta = [entry(80, { page_no: 400, event_date: "2023-06-06" })] as Entries;
      outline = { status: "ready", coverage: COVERAGE, sections: [sectionOf("alpha", alpha), sectionOf("beta", beta, alpha.length)] };
      mockTranscript("ready", [...alpha, ...beta]);
      let release: () => void = () => {};
      const gate = new Promise<void>((r) => (release = r));
      server.use(
        http.get(`${C}/transcript/entries`, async ({ request }) => {
          const offset = Number(new URL(request.url).searchParams.get("offset") ?? 0);
          if (offset !== 50) return undefined;
          await gate;
          return undefined;
        }),
      );
      const { user } = renderAt(SECTION);
      await screen.findByLabelText("Entry on page 100");
      await user.click(within(screen.getByRole("group", { name: "Jump to a year" })).getByRole("button", { name: "2023" }));
      await user.click(screen.getByRole("link", { name: /Next section: Beta/ }));
      await screen.findByLabelText("Entry on page 400");
      (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mockClear();
      release();
      await new Promise((r) => setTimeout(r, 100));
      expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
    });

    test("a year not loaded yet loads forward, then scrolls to its heading and records no read", async () => {
      mockReader(pack);
      const early = Array.from({ length: 60 }, (_, i) => entry(i + 1, { id: `00000000-0000-0000-0000-${String(i + 1).padStart(12, "0")}`, page_no: 100 + i, event_date: "2019-05-05" }));
      const late = [entry(70, { page_no: 300, event_date: "2023-02-02" })];
      const all = [...early, ...late] as Entries;
      outline = { status: "ready", coverage: COVERAGE, sections: [sectionOf("alpha", all)] };
      const log = mockTranscript("ready", all);
      const { user } = renderAt(SECTION);
      await screen.findByLabelText("Entry on page 100");
      expect(screen.queryByLabelText("Entry on page 300")).toBeNull();
      (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mockClear();
      await user.click(within(screen.getByRole("group", { name: "Jump to a year" })).getByRole("button", { name: "2023" }));
      expect(await screen.findByLabelText("Entry on page 300")).toBeVisible();
      expect(log.requests).toContainEqual({ offset: 50, limit: 11 });
      await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled());
      const scrolled = (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mock.contexts.at(-1) as HTMLElement;
      expect(scrolled.textContent).toBe("2023");
      expect(log.seen).toEqual([]);
    });

    test("Next section opens the following section, and the last one leads on to the first appendix", async () => {
      mockReader(pack);
      withOutline();
      mockTranscript("ready", sectioned(), [3]);
      const { user, router } = renderAt(`${PATH}?section=alpha`);
      await user.click(await screen.findByRole("link", { name: /Next section: Beta/ }));
      expect(await screen.findByRole("heading", { level: 2, name: "2. Beta" })).toBeVisible();
      expect(router.state.location.search).toBe("?section=beta");
      expect(screen.getByRole("link", { name: /Next section: Appendix A · Pages with no entries/ })).toBeVisible();
    });

    test("appendix rows show only when there are such pages, and open their pages", async () => {
      mockReader(pack);
      withOutline();
      mockTranscript("ready", sectioned());
      const first = renderAt(PATH);
      await screen.findByRole("link", { name: /Alpha/ });
      expect(screen.queryByRole("link", { name: /Appendix/ })).toBeNull();
      first.unmount();

      mockTranscript("ready", sectioned(), [3], [7, 8]);
      const { user, router } = renderAt(PATH);
      expect(await screen.findByRole("link", { name: "Appendix A · Pages with no entries (1)" })).toBeVisible();
      await user.click(screen.getByRole("link", { name: "Appendix B · Pages we couldn't read (2)" }));
      expect(router.state.location.search).toBe("?section=_unread");
      expect(await screen.findByRole("button", { name: "Open the picture of page 7" })).toBeVisible();
      expect(screen.getByRole("link", { name: "← Contents" })).toBeVisible();
    });

    test("the sections' contents are within reach beside the section, with the current one marked", async () => {
      mockReader(pack);
      withOutline();
      mockTranscript("ready", sectioned());
      renderAt(`${PATH}?section=beta`);
      const nav = await screen.findByRole("navigation", { name: "Contents" });
      expect(nav.querySelector("[aria-current=true]")).toHaveTextContent(/Beta/);
      expect(nav.querySelectorAll("[aria-current=true]")).toHaveLength(1);
    });

    test("the section and appendix views have no violations", async () => {
      mockReader(pack);
      withOutline();
      mockTranscript("ready", sectioned(), [3], [7]);
      const section = renderAt(`${PATH}?section=alpha`);
      await section.findByLabelText("Entry on page 11");
      expect(await axeViolations(section.container)).toEqual([]);
      section.unmount();
      const appendix = renderAt(`${PATH}?section=_no_entries`);
      await appendix.findByRole("button", { name: "Open the picture of page 3" });
      expect(await axeViolations(appendix.container)).toEqual([]);
    });
  });
});
