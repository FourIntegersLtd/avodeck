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
  closed = false;
  constructor(public url: string) {
    FakeSource.all.push(this);
  }
  close() {
    this.closed = true;
  }
  send(data: object) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent<string>);
  }
}

const WORDS = { done: "Your new papers are ready.", failed: "We could not finish your new papers.", open: "Open your papers", to: "/cases/c1/bundle" };

function Starter() {
  const { watch } = useJobWatch();
  return <button onClick={() => watch("job-1", WORDS)}>start</button>;
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
    expect(FakeSource.all[0]!.url).toContain("/v1/jobs/job-1/events");
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

  test("watching the same job twice listens once", async () => {
    mount();
    await userEvent.click(screen.getByText("start"));
    await userEvent.click(screen.getByText("start"));
    expect(FakeSource.all).toHaveLength(1);
  });
});
