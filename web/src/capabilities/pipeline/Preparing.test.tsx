// Screen 07, Preparing your bundle: follows the case's latest processing job over the job's event
// stream, shows each step, and opens screen 08 when the bundle is ready. Runs under both pack sets.
import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { registerScreen } from "../../foundation/lib/screens";
import { apiError, http, json, server, signedIn } from "../../test/api";
import { fixturePack, launchPack } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { CASE_ID, JOB, NEXT_JOB, processingJob, summary, theCase, VIEWER_ACTIONS } from "./testData";
import type { Schemas } from "../../foundation/platform-client";

class FakeEventSource {
  static all: FakeEventSource[] = [];
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  closed = false;
  private listeners: Array<(e: MessageEvent) => void> = [];
  constructor(
    public url: string,
    public init?: { withCredentials?: boolean },
  ) {
    FakeEventSource.all.push(this);
  }
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    if (type === "message") this.listeners.push(fn);
  }
  removeEventListener() {}
  close() {
    this.closed = true;
  }
  emit(data: object) {
    const e = new MessageEvent("message", { data: JSON.stringify(data) });
    this.onmessage?.(e);
    this.listeners.forEach((fn) => fn(e));
  }
}

const latest = () => FakeEventSource.all[FakeEventSource.all.length - 1]!;

beforeEach(() => {
  FakeEventSource.all = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});
afterEach(() => vi.unstubAllGlobals());

const PATH = `/cases/${CASE_ID}/preparing`;

function mockCase(pack: Schemas["CasePack"], opts: { job?: Schemas["ProcessingJob"] | null; item?: Schemas["Case"]; files?: number } = {}) {
  signedIn({ pack });
  const files = opts.files ?? 3;
  const docs = Array.from({ length: files }, (_, i) => ({ id: `d${i}`, kind: "original", filename: `f${i}.pdf`, pages: 40, first_page_no: null, last_page_no: null, received_on: null, obtained_how: null, bundle_version: 1 }));
  server.use(
    http.get(`/v1/cases/${CASE_ID}`, () => json(opts.item ?? theCase())),
    http.get(`/v1/cases/${CASE_ID}/process`, () => json({ job: opts.job === undefined ? processingJob() : opts.job })),
    http.get(`/v1/cases/${CASE_ID}/bundle/summary`, () =>
      json(summary(pack, { pages: 120, documents: docs as unknown as Schemas["BundleSummary"]["documents"], quarantine_suggestions: [], foreign_language: [] })),
    ),
    http.get(`/v1/cases/${CASE_ID}/missing-items`, () => json({ missing_items: [] })),
  );
}

const stepRow = (name: string) => screen.getByText(name).closest("li")!;

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("Preparing under the $name", ({ pack }) => {
  test("shows the size, time left and each step, following the job's event stream", async () => {
    mockCase(pack);
    const { container } = renderAt(PATH);
    expect(await screen.findByRole("heading", { level: 1, name: "Preparing your bundle" })).toBeVisible();
    expect(await screen.findByText("120 pages from 3 files. About 4 minutes left.")).toBeVisible();
    await waitFor(() => expect(FakeEventSource.all.length).toBe(1));
    expect(latest().url).toMatch(new RegExp(`/v1/jobs/${JOB}/events$`));
    expect(latest().init?.withCredentials).toBe(true);
    expect(within(stepRow("Splitting into pages")).getByText("Done")).toBeInTheDocument();
    expect(within(stepRow("Reading every page")).getByText("Done")).toBeInTheDocument();
    const sorting = stepRow("Sorting by where each page came from");
    expect(within(sorting).getByText("Doing this now")).toBeInTheDocument();
    expect(within(sorting).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "48");
    expect(within(stepRow("Setting aside copies of the same page")).getByText("Not started yet")).toBeInTheDocument();
    expect(within(stepRow("Checking what looks missing")).getByText("Not started yet")).toBeInTheDocument();
    expect(screen.getByText("Your original files are kept exactly as you sent them.")).toBeVisible();
    expect(screen.getByText("You can close this window. We'll email you when it's ready.")).toBeVisible();
    expect(await axeViolations(container)).toEqual([]);
  });

  test("the pack step that holds this screen is the current step", async () => {
    mockCase(pack);
    renderAt(PATH);
    await screen.findByText("Splitting into pages");
    const steps = pack.workflow_steps as Array<{ key: string; screens?: string[] }>;
    const index = steps.findIndex((s) => s.screens?.includes("preparing"));
    const items = screen.getAllByRole("listitem").filter((li) => li.classList.contains("av-step"));
    expect(items[index]).toHaveAttribute("aria-current", "step");
  });

  test("an event moves the steps on and updates the time left", async () => {
    mockCase(pack);
    renderAt(PATH);
    await waitFor(() => expect(FakeEventSource.all.length).toBe(1));
    act(() => latest().emit({ status: "running", step: "duplicates", pct: 60, eta_seconds: 50, error_code: null }));
    expect(await screen.findByText("120 pages from 3 files. About 1 minute left.")).toBeVisible();
    expect(within(stepRow("Sorting by where each page came from")).getByText("Done")).toBeInTheDocument();
    expect(within(stepRow("Setting aside copies of the same page")).getByText("Doing this now")).toBeInTheDocument();
  });

  test("when the job is done, it opens Your bundle is ready", async () => {
    mockCase(pack);
    const { router } = renderAt(PATH);
    await waitFor(() => expect(FakeEventSource.all.length).toBe(1));
    server.use(http.get(`/v1/cases/${CASE_ID}/process`, () => json({ job: processingJob({ status: "done", step: "missing", pct: 100, eta_seconds: 0 }) })));
    act(() => latest().emit({ status: "done", step: "missing", pct: 100, eta_seconds: 0, error_code: null }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/cases/${CASE_ID}/bundle`));
    expect(latest().closed).toBe(true);
  });

  test("when the upload check finishes, it follows the processing job that comes next", async () => {
    mockCase(pack, { job: processingJob({ kind: "scan_document", step: "scan", pct: 10, eta_seconds: null }) });
    const { router } = renderAt(PATH);
    await waitFor(() => expect(FakeEventSource.all.length).toBe(1));
    expect(within(stepRow("Splitting into pages")).getByText("Not started yet")).toBeInTheDocument();
    server.use(http.get(`/v1/cases/${CASE_ID}/process`, () => json({ job: processingJob({ id: NEXT_JOB, status: "queued", step: null, pct: 0, eta_seconds: null }) })));
    act(() => latest().emit({ status: "done", step: "scan", pct: 100, eta_seconds: 0, error_code: null }));
    await waitFor(() => expect(FakeEventSource.all.length).toBe(2));
    expect(latest().url).toMatch(new RegExp(`/v1/jobs/${NEXT_JOB}/events$`));
    expect(router.state.location.pathname).toBe(PATH);
  });

  test("a job that is already done opens Your bundle is ready straight away", async () => {
    mockCase(pack, { job: processingJob({ status: "done", step: "missing", pct: 100, eta_seconds: 0 }) });
    const { router } = renderAt(PATH);
    await waitFor(() => expect(router.state.location.pathname).toBe(`/cases/${CASE_ID}/bundle`));
  });

  test("with nothing started, the expert can start preparing", async () => {
    mockCase(pack, { job: null });
    let started = 0;
    server.use(
      http.post(`/v1/cases/${CASE_ID}/process`, () => {
        started += 1;
        return json({ job_id: JOB }, 202);
      }),
    );
    const { user, container } = renderAt(PATH);
    const start = await screen.findByRole("button", { name: "Start preparing" });
    expect(await axeViolations(container)).toEqual([]);
    await user.click(start);
    await waitFor(() => expect(FakeEventSource.all.length).toBe(1));
    expect(started).toBe(1);
    expect(latest().url).toMatch(new RegExp(`/v1/jobs/${JOB}/events$`));
  });

  test("Stop preparing cancels the job and offers to start again", async () => {
    mockCase(pack);
    let cancelled = 0;
    server.use(
      http.post(`/v1/cases/${CASE_ID}/process/cancel`, () => {
        cancelled += 1;
        return json({ cancelled: 1 }, 202);
      }),
    );
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Stop preparing" }));
    expect(await screen.findByText("Preparing has stopped. Your files are kept.")).toBeVisible();
    expect(cancelled).toBe(1);
    expect(screen.getByRole("button", { name: "Start preparing" })).toBeVisible();
    expect(latest().closed).toBe(true);
  });

  test("someone who cannot add records sees the progress but no Stop or Start", async () => {
    mockCase(pack, { item: theCase({ allowed_actions: VIEWER_ACTIONS }) });
    renderAt(PATH);
    await screen.findByText("Splitting into pages");
    expect(screen.queryByRole("button", { name: "Stop preparing" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Start preparing" })).toBeNull();
  });

  test("a failed job says so in plain words and offers to try again", async () => {
    mockCase(pack);
    server.use(http.post(`/v1/cases/${CASE_ID}/process`, () => json({ job_id: NEXT_JOB }, 202)));
    const { user } = renderAt(PATH);
    await waitFor(() => expect(FakeEventSource.all.length).toBe(1));
    act(() => latest().emit({ status: "failed", step: "ocr", pct: 35, eta_seconds: null, error_code: "ocr_failed" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong while preparing your bundle. Your files are kept.");
    expect(alert).not.toHaveTextContent("ocr_failed");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(latest().url).toMatch(new RegExp(`/v1/jobs/${NEXT_JOB}/events$`)));
  });

  test("files Avodeck cannot read are listed, and the questions screen is offered when it exists", async () => {
    registerScreen("questions", ({ caseId }) => `/cases/${caseId}/questions`);
    mockCase(pack);
    renderAt(PATH);
    expect(await screen.findByText("1 recording or image was kept as it is. Avodeck can't read these, so it's listed for you.")).toBeVisible();
    expect(screen.getByRole("link", { name: "Meanwhile, check what you've been asked" })).toHaveAttribute("href", `/cases/${CASE_ID}/questions`);
  });

  test("a case that cannot be loaded shows the shared error", async () => {
    signedIn({ pack });
    server.use(
      http.get(`/v1/cases/${CASE_ID}`, () => json(theCase())),
      http.get(`/v1/cases/${CASE_ID}/process`, () => apiError(500, "server_error")),
      http.get(`/v1/cases/${CASE_ID}/bundle/summary`, () => apiError(500, "server_error")),
    );
    renderAt(PATH);
    expect(await screen.findByRole("button", { name: "Try again" })).toBeVisible();
  });
});
