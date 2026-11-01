// useEventStream: opens an EventSource when the browser has one, falls back to interval polling
// otherwise, and cleans up either way on unmount or when the url changes.
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { useEventStream } from "./sse";

class FakeEventSource {
  static all: FakeEventSource[] = [];
  onmessage: ((e: MessageEvent) => void) | null = null;
  closed = false;
  constructor(
    public url: string,
    public init?: { withCredentials?: boolean },
  ) {
    FakeEventSource.all.push(this);
  }
  close() {
    this.closed = true;
  }
  emit(data: object) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(data) }));
  }
}

const latest = () => FakeEventSource.all[FakeEventSource.all.length - 1]!;

describe("with EventSource available", () => {
  beforeEach(() => {
    FakeEventSource.all = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });
  afterEach(() => vi.unstubAllGlobals());

  test("opens a stream with credentials and calls onUpdate on a message", () => {
    const onUpdate = vi.fn();
    renderHook(() => useEventStream("/v1/jobs/1/events", onUpdate, vi.fn(), 1000));
    expect(FakeEventSource.all).toHaveLength(1);
    expect(latest().url).toBe("/v1/jobs/1/events");
    expect(latest().init).toEqual({ withCredentials: true });
    latest().emit({ status: "running" });
    expect(onUpdate).toHaveBeenCalledWith({ status: "running" });
  });

  test("never falls back to polling while EventSource works", () => {
    const pollFallback = vi.fn(() => Promise.resolve({}));
    renderHook(() => useEventStream("/v1/jobs/1/events", vi.fn(), pollFallback, 10));
    expect(pollFallback).not.toHaveBeenCalled();
  });

  test("closes the stream on unmount", () => {
    const { unmount } = renderHook(() => useEventStream("/v1/jobs/1/events", vi.fn(), vi.fn(), 1000));
    unmount();
    expect(latest().closed).toBe(true);
  });

  test("closes the old stream and opens a new one when the url changes", () => {
    const { rerender } = renderHook(({ url }) => useEventStream(url, vi.fn(), vi.fn(), 1000), {
      initialProps: { url: "/v1/jobs/1/events" },
    });
    const first = latest();
    rerender({ url: "/v1/jobs/2/events" });
    expect(first.closed).toBe(true);
    expect(FakeEventSource.all).toHaveLength(2);
    expect(latest().url).toBe("/v1/jobs/2/events");
  });

  test("opens nothing when the url is null", () => {
    renderHook(() => useEventStream(null, vi.fn(), vi.fn(), 1000));
    expect(FakeEventSource.all).toHaveLength(0);
  });
});

describe("without EventSource", () => {
  let real: typeof EventSource | undefined;
  beforeEach(() => {
    vi.useFakeTimers();
    real = globalThis.EventSource;
    // @ts-expect-error -- simulate a browser with no EventSource support
    delete globalThis.EventSource;
  });
  afterEach(() => {
    vi.useRealTimers();
    globalThis.EventSource = real as typeof EventSource;
  });

  test("polls on an interval instead", async () => {
    const onUpdate = vi.fn();
    const pollFallback = vi.fn(() => Promise.resolve({ status: "running" }));
    renderHook(() => useEventStream("/v1/jobs/1/events", onUpdate, pollFallback, 1000));
    await vi.advanceTimersByTimeAsync(1000);
    expect(pollFallback).toHaveBeenCalledTimes(1);
    expect(onUpdate).toHaveBeenCalledWith({ status: "running" });
    await vi.advanceTimersByTimeAsync(1000);
    expect(pollFallback).toHaveBeenCalledTimes(2);
  });

  test("stops polling on unmount", async () => {
    const pollFallback = vi.fn(() => Promise.resolve({}));
    const { unmount } = renderHook(() => useEventStream("/v1/jobs/1/events", vi.fn(), pollFallback, 1000));
    unmount();
    await vi.advanceTimersByTimeAsync(5000);
    expect(pollFallback).not.toHaveBeenCalled();
  });

  test("does not poll when the url is null", async () => {
    const pollFallback = vi.fn(() => Promise.resolve({}));
    renderHook(() => useEventStream(null, vi.fn(), pollFallback, 1000));
    await vi.advanceTimersByTimeAsync(5000);
    expect(pollFallback).not.toHaveBeenCalled();
  });
});
