// The live hub: the one place that opens an EventSource. One stream per open case is shared by every
// screen that listens to it, closed when the last one leaves, and nothing ever polls.
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { apiOrigin } from "../platform-client";
import { http, json, server } from "../../test/api";
import { useCaseJobs, useJob, useOnCaseChange, type LiveJob } from "./sse";

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
