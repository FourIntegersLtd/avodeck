// A watched job tells the person when it ends, on any screen, with a toast they can follow or close.
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { JobWatchProvider, useJobWatch } from "./jobWatch";
import { ToastProvider } from "./toasts";

class FakeSource {
  static all: FakeSource[] = [];
  onmessage: ((e: MessageEvent<string>) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  readyState = 1;
  closed = false;
  constructor(public url: string) {
    FakeSource.all.push(this);
  }
  close() {
    this.closed = true;
  }
  send(job: object) {
    const full = { id: "job-1", kind: "append_documents", status: "running", step: null, pct: 0, eta_seconds: null, step_done: null, step_total: null, error_code: null, ...job };
    this.onmessage?.({ data: JSON.stringify({ jobs: [full] }) } as MessageEvent<string>);
  }
}

const WORDS = { done: "Your new papers are ready.", failed: "We could not finish your new papers.", open: "Open your papers", to: "/cases/c1/bundle" };

function Starter() {
  const { watch } = useJobWatch();
  return <button onClick={() => watch("c1", "job-1", WORDS)}>start</button>;
}

function mount() {
  render(
    <MemoryRouter>
      <ToastProvider>
        <JobWatchProvider>
          <Starter />
        </JobWatchProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  FakeSource.all = [];
  vi.stubGlobal("EventSource", FakeSource);
});
afterEach(() => vi.unstubAllGlobals());

describe("job watch", () => {
  test("shows a toast with a link when the job is done, and stops listening", async () => {
    mount();
    await userEvent.click(screen.getByText("start"));
    expect(FakeSource.all[0]!.url).toContain("/v1/cases/c1/events");
    expect(screen.queryByText(WORDS.done)).toBeNull();
    act(() => FakeSource.all[0]!.send({ status: "running", pct: 40 }));
    expect(screen.queryByText(WORDS.done)).toBeNull();
    act(() => FakeSource.all[0]!.send({ status: "done", pct: 100 }));
    expect(screen.getByText(WORDS.done)).toBeTruthy();
    expect(screen.getByRole("link", { name: WORDS.open }).getAttribute("href")).toBe(WORDS.to);
    expect(FakeSource.all[0]!.closed).toBe(true);
  });

  test("says so when the job fails, and the toast can be closed", async () => {
    mount();
    await userEvent.click(screen.getByText("start"));
    act(() => FakeSource.all[0]!.send({ status: "failed", error_code: "internal" }));
    expect(screen.getByText(WORDS.failed)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByText(WORDS.failed)).toBeNull();
  });

  test("watching two jobs of the same case shares one stream", async () => {
    function Two() {
      const { watch } = useJobWatch();
      return (
        <button
          onClick={() => {
            watch("c1", "job-1", WORDS);
            watch("c1", "job-2", WORDS);
          }}
        >
          both
        </button>
      );
    }
    render(
      <MemoryRouter>
        <ToastProvider>
          <JobWatchProvider>
            <Two />
          </JobWatchProvider>
        </ToastProvider>
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByText("both"));
    expect(FakeSource.all).toHaveLength(1);
  });

  test("watching the same job twice listens once", async () => {
    mount();
    await userEvent.click(screen.getByText("start"));
    await userEvent.click(screen.getByText("start"));
    expect(FakeSource.all).toHaveLength(1);
  });
});
