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
  folded: false,
  author_role: null,
  specialty: null,
  event_date: "2021-03-03",
  date_basis: null,
  lines: [`line ${n}`],
  results: [],
  ...over,
});

interface Seen {
  seen: unknown[];
  downloads: number;
}

function mockTranscript(status: "ready" | "preparing" | "failed", entries = [entry(1), entry(2, { folded: true })]): Seen {
  const log: Seen = { seen: [], downloads: 0 };
  server.use(
    http.get(`${C}/transcript/entries`, () => json({ status, total: entries.length, entries })),
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

beforeEach(() => {
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
    expect(await screen.findByText("We couldn't get the record ready.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Try again" })).toBeVisible();
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
});
