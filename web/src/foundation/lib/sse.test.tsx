// The live hub: the one place that opens an EventSource. One stream per open case is shared by every
// screen that listens to it, closed when the last one leaves, and nothing ever polls.
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { apiOrigin } from "../platform-client";
import { http, json, server } from "../../test/api";
import { useCaseJobs, useJob, useOnCaseChange, followCase, waitForCaseChange, waitForJob, type LiveJob } from "./sse";

class FakeEventSource {
  static all: FakeEventSource[] = [];
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  readyState = 1;
  closed = false;
  constructor(
    public url: string,
    public init?: { withCredentials?: boolean },
  ) {
    FakeEventSource.all.push(this);
  }
  close() {
    this.closed = true;
    this.readyState = 2;
  }
  emit(jobs: LiveJob[]) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify({ jobs }) }));
  }
  refuse() {
    this.readyState = 2;
    this.onerror?.(new Event("error"));
  }
}

const job = (over: Partial<LiveJob> = {}): LiveJob => ({
  id: "j1",
  kind: "process_bundle",
  status: "running",
  step: "ocr",
  pct: 10,
  eta_seconds: null,
  step_done: 0,
  step_total: 100,
  error_code: null,
  ...over,
});
const latest = () => FakeEventSource.all[FakeEventSource.all.length - 1]!;
const url = (caseId: string) => `${apiOrigin()}/v1/cases/${caseId}/events`;

beforeEach(() => {
  FakeEventSource.all = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("one stream per open case", () => {
  test("opens the case stream with credentials and gives its jobs once the first snapshot arrives", () => {
    const { result } = renderHook(() => useCaseJobs("c1"));
    expect(FakeEventSource.all).toHaveLength(1);
    expect(latest().url).toBe(url("c1"));
    expect(latest().init).toEqual({ withCredentials: true });
    expect(result.current).toBeNull();
    act(() => latest().emit([job()]));
    expect(result.current).toEqual([job()]);
  });

  test("two listeners on the same case share one stream, and it closes when the last one leaves", () => {
    const a = renderHook(() => useCaseJobs("c1"));
    const b = renderHook(() => useJob("c1", "j1"));
    expect(FakeEventSource.all).toHaveLength(1);
    act(() => latest().emit([job({ step_done: 275, step_total: 882 })]));
    expect(a.result.current?.[0]?.step_done).toBe(275);
    expect(b.result.current?.step_total).toBe(882);
    a.unmount();
    expect(latest().closed).toBe(false);
    b.unmount();
    expect(latest().closed).toBe(true);
  });

  test("different cases get different streams", () => {
    renderHook(() => useCaseJobs("c1"));
    renderHook(() => useCaseJobs("c2"));
    expect(FakeEventSource.all.map((s) => s.url)).toEqual([url("c1"), url("c2")]);
  });

  test("a case that is null opens nothing, and moving to null closes the stream", () => {
    renderHook(() => useCaseJobs(null));
    expect(FakeEventSource.all).toHaveLength(0);
    const { rerender } = renderHook(({ id }: { id: string | null }) => useCaseJobs(id), { initialProps: { id: "c1" as string | null } });
    const first = latest();
    rerender({ id: null });
    expect(first.closed).toBe(true);
  });

  test("listening again after everyone left opens a fresh stream and starts from nothing", () => {
    const first = renderHook(() => useCaseJobs("c1"));
    act(() => latest().emit([job()]));
    first.unmount();
    const again = renderHook(() => useCaseJobs("c1"));
    expect(FakeEventSource.all).toHaveLength(2);
    expect(again.result.current).toBeNull();
  });
});

describe("useJob", () => {
  test("gives the one job asked for, or null when it is not in the snapshot", () => {
    const { result } = renderHook(() => useJob("c1", "j2"));
    act(() => latest().emit([job({ id: "j1" })]));
    expect(result.current).toBeNull();
    act(() => latest().emit([job({ id: "j1" }), job({ id: "j2", status: "done" })]));
    expect(result.current?.status).toBe("done");
  });

  test("keeps the same object while that job has not changed, whatever the others do", () => {
    const { result } = renderHook(() => useJob("c1", "j1"));
    act(() => latest().emit([job({ id: "j1" }), job({ id: "j2", pct: 1 })]));
    const before = result.current;
    act(() => latest().emit([job({ id: "j1" }), job({ id: "j2", pct: 2 })]));
    expect(result.current).toBe(before);
    act(() => latest().emit([job({ id: "j1", pct: 50 }), job({ id: "j2", pct: 2 })]));
    expect(result.current).not.toBe(before);
    expect(result.current?.pct).toBe(50);
  });

  test("with no job id it follows nothing", () => {
    const { result } = renderHook(() => useJob("c1", null));
    act(() => latest().emit([job()]));
    expect(result.current).toBeNull();
  });
});

describe("useOnCaseChange", () => {
  beforeEach(() => vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] }));

  test("looks again once when the first snapshot arrives, in case a job ended before the stream opened", () => {
    const reload = vi.fn();
    renderHook(() => useOnCaseChange("c1", reload));
    expect(reload).not.toHaveBeenCalled();
    act(() => latest().emit([job()]));
    expect(reload).toHaveBeenCalledTimes(1);
  });

  test("does not look again when only the counts move, however often they do", () => {
    const reload = vi.fn();
    renderHook(() => useOnCaseChange("c1", reload));
    act(() => latest().emit([job()]));
    reload.mockClear();
    for (let done = 1; done <= 200; done++) {
      act(() => latest().emit([job({ step_done: done, pct: done % 100 })]));
      act(() => void vi.advanceTimersByTime(3000));
    }
    expect(reload).not.toHaveBeenCalled();
  });

  test("looks again when a job's status changes, a job appears, or a job goes", () => {
    const reload = vi.fn();
    renderHook(() => useOnCaseChange("c1", reload));
    act(() => latest().emit([job()]));
    reload.mockClear();
    act(() => void vi.advanceTimersByTime(3000));
    act(() => latest().emit([job({ status: "done" })]));
    expect(reload).toHaveBeenCalledTimes(1);
    act(() => void vi.advanceTimersByTime(3000));
    act(() => latest().emit([job({ status: "done" }), job({ id: "j2", status: "queued" })]));
    expect(reload).toHaveBeenCalledTimes(2);
    act(() => void vi.advanceTimersByTime(3000));
    act(() => latest().emit([job({ id: "j2", status: "queued" })]));
    expect(reload).toHaveBeenCalledTimes(3);
  });

  test("looks again at most once every two seconds, and never loses the last change", () => {
    const reload = vi.fn();
    renderHook(() => useOnCaseChange("c1", reload));
    act(() => latest().emit([job({ status: "queued" })]));
    reload.mockClear();
    act(() => void vi.advanceTimersByTime(3000));
    act(() => latest().emit([job({ status: "running" })]));
    expect(reload).toHaveBeenCalledTimes(1);
    act(() => latest().emit([job({ status: "paused" })]));
    act(() => latest().emit([job({ status: "failed" })]));
    expect(reload).toHaveBeenCalledTimes(1);
    act(() => void vi.advanceTimersByTime(2100));
    expect(reload).toHaveBeenCalledTimes(2);
    act(() => void vi.advanceTimersByTime(10_000));
    expect(reload).toHaveBeenCalledTimes(2);
  });

  test("a case that is null follows nothing, and waiting again starts with a fresh look", () => {
    const reload = vi.fn();
    const { rerender } = renderHook(({ id }: { id: string | null }) => useOnCaseChange(id, reload), { initialProps: { id: null as string | null } });
    expect(FakeEventSource.all).toHaveLength(0);
    rerender({ id: "c1" });
    act(() => latest().emit([job()]));
    expect(reload).toHaveBeenCalledTimes(1);
    rerender({ id: null });
    act(() => void vi.advanceTimersByTime(3000));
    rerender({ id: "c1" });
    act(() => latest().emit([job()]));
    expect(reload).toHaveBeenCalledTimes(2);
  });

  test("a look that was waiting does not happen after the screen has gone", () => {
    const reload = vi.fn();
    const { unmount } = renderHook(() => useOnCaseChange("c1", reload));
    act(() => latest().emit([job({ status: "queued" })]));
    act(() => latest().emit([job({ status: "running" })]));
    reload.mockClear();
    unmount();
    act(() => void vi.advanceTimersByTime(5000));
    expect(reload).not.toHaveBeenCalled();
  });

  test("always calls the newest version of the function it was given", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = renderHook(({ fn }) => useOnCaseChange("c1", fn), { initialProps: { fn: first } });
    act(() => latest().emit([job({ status: "queued" })]));
    first.mockClear();
    act(() => void vi.advanceTimersByTime(3000));
    rerender({ fn: second });
    act(() => latest().emit([job({ status: "done" })]));
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});

describe("a stream the server refuses", () => {
  test("checks the session the normal way, so a signed-out person is sent to sign in", async () => {
    let checked = 0;
    server.use(
      http.get("/v1/auth/session", () => {
        checked += 1;
        return json({ must_change_password: false });
      }),
    );
    renderHook(() => useCaseJobs("c1"));
    latest().refuse();
    await vi.waitFor(() => expect(checked).toBe(1));
  });

  test("opens a fresh stream after a pause, and not again once nobody is listening", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      const { unmount } = renderHook(() => useCaseJobs("c1"));
      const first = latest();
      first.refuse();
      expect(FakeEventSource.all).toHaveLength(1);
      act(() => void vi.advanceTimersByTime(2000));
      expect(FakeEventSource.all).toHaveLength(2);
      expect(latest().url).toBe(url("c1"));
      act(() => latest().emit([job()]));
      latest().refuse();
      unmount();
      act(() => void vi.advanceTimersByTime(60_000));
      expect(FakeEventSource.all).toHaveLength(2); // the last listener left, so nothing is reopened
    } finally {
      vi.useRealTimers();
    }
  });

  test("a stream that is only reconnecting by itself is left alone", async () => {
    let checked = 0;
    server.use(
      http.get("/v1/auth/session", () => {
        checked += 1;
        return json({ must_change_password: false });
      }),
    );
    renderHook(() => useCaseJobs("c1"));
    latest().readyState = 0; // CONNECTING: the browser is retrying
    latest().onerror?.(new Event("error"));
    await new Promise((r) => setTimeout(r, 20));
    expect(checked).toBe(0);
  });
});

describe("waitForJob", () => {
  test("settles with the job when it ends, then lets the stream go", async () => {
    const wait = waitForJob("c1", "j1", { timeoutMs: 60_000 });
    latest().emit([job({ status: "running" })]);
    latest().emit([job({ status: "done" })]);
    await expect(wait).resolves.toMatchObject({ id: "j1", status: "done" });
    expect(latest().closed).toBe(true);
  });

  test("a failed or cancelled job settles too, with its status", async () => {
    const wait = waitForJob("c1", "j1", { timeoutMs: 60_000 });
    latest().emit([job({ status: "failed", error_code: "build_failed" })]);
    await expect(wait).resolves.toMatchObject({ status: "failed", error_code: "build_failed" });
  });

  test("a job that ended before the stream opened is found in the first snapshot", async () => {
    const wait = waitForJob("c1", "j1", { timeoutMs: 60_000 });
    latest().emit([job({ status: "done" }), job({ id: "j2", status: "running" })]);
    await expect(wait).resolves.toMatchObject({ id: "j1", status: "done" });
  });

  test("ignores other jobs of the case", async () => {
    const wait = waitForJob("c1", "j1", { timeoutMs: 60_000 });
    let settled = false;
    void wait.then(() => (settled = true), () => (settled = true));
    latest().emit([job({ id: "j2", status: "done" }), job({ id: "j1", status: "running" })]);
    await Promise.resolve();
    expect(settled).toBe(false);
    latest().emit([job({ id: "j1", status: "done" })]);
    await expect(wait).resolves.toMatchObject({ id: "j1" });
  });

  test("rejects when asked to stop, and lets the stream go", async () => {
    const stop = new AbortController();
    const wait = waitForJob("c1", "j1", { signal: stop.signal, timeoutMs: 60_000 });
    const source = latest();
    stop.abort();
    await expect(wait).rejects.toMatchObject({ name: "AbortError" });
    expect(source.closed).toBe(true);
  });

  test("rejects when it takes longer than allowed", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const wait = waitForJob("c1", "j1", { timeoutMs: 5000 });
    const caught = wait.catch((e: Error) => e);
    latest().emit([job({ status: "running" })]);
    await vi.advanceTimersByTimeAsync(5001);
    expect(await caught).toMatchObject({ name: "TimeoutError" });
    expect(latest().closed).toBe(true);
  });
});

describe("waitForCaseChange", () => {
  test("with no snapshot yet it settles on the first one", async () => {
    const wait = waitForCaseChange("c1", { timeoutMs: 60_000 });
    latest().emit([job({ status: "running" })]);
    await expect(wait).resolves.toBeUndefined();
    expect(latest().closed).toBe(true);
  });

  test("with a snapshot held it settles when a job's status changes, a job appears or goes, never on counts", async () => {
    renderHook(() => useCaseJobs("c1")); // another screen keeps the stream open
    latest().emit([job({ status: "running" })]);
    const next = waitForCaseChange("c1", { timeoutMs: 60_000 });
    let settled = false;
    void next.then(() => (settled = true));
    latest().emit([job({ status: "running", step_done: 5 })]);
    await Promise.resolve();
    expect(settled).toBe(false);
    latest().emit([job({ status: "done" })]);
    await expect(next).resolves.toBeUndefined();
    expect(latest().closed).toBe(false);
  });

  test("rejects on stop or timeout", async () => {
    const stop = new AbortController();
    const wait = waitForCaseChange("c1", { signal: stop.signal, timeoutMs: 60_000 });
    stop.abort();
    await expect(wait).rejects.toMatchObject({ name: "AbortError" });
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const slow = waitForCaseChange("c1", { timeoutMs: 1000 }).catch((e: Error) => e);
    await vi.advanceTimersByTimeAsync(1001);
    expect(await slow).toMatchObject({ name: "TimeoutError" });
  });
});

describe("followCase", () => {
  test("one stream is held across waits; each wait settles on a change, and a change between waits is not lost", async () => {
    const follow = followCase("c1");
    expect(FakeEventSource.all).toHaveLength(1);
    const first = follow.changed({ timeoutMs: 60_000 });
    latest().emit([job({ status: "running" })]);
    await expect(first).resolves.toBeUndefined(); // the first snapshot
    latest().emit([job({ status: "done" })]); // changes while nobody is waiting
    await expect(follow.changed({ timeoutMs: 60_000 })).resolves.toBeUndefined();
    let settled = false;
    const next = follow.changed({ timeoutMs: 60_000 });
    void next.then(() => (settled = true));
    latest().emit([job({ status: "done", step_done: 9 })]); // counts only
    await Promise.resolve();
    expect(settled).toBe(false);
    expect(FakeEventSource.all).toHaveLength(1);
    follow.close();
    expect(latest().closed).toBe(true);
    await expect(Promise.race([next, Promise.resolve("pending")])).resolves.toBe("pending");
  });

  test("rejects on stop or timeout", async () => {
    const follow = followCase("c1");
    const stop = new AbortController();
    const waiting = follow.changed({ signal: stop.signal, timeoutMs: 60_000 });
    stop.abort();
    await expect(waiting).rejects.toMatchObject({ name: "AbortError" });
    vi.useFakeTimers();
    const slow = follow.changed({ timeoutMs: 1000 });
    const check = expect(slow).rejects.toMatchObject({ name: "TimeoutError" });
    await vi.advanceTimersByTimeAsync(1001);
    await check;
    vi.useRealTimers();
    follow.close();
  });
});
