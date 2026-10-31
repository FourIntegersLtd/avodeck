import { act, renderHook } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { useBusy } from "./useBusy";

test("run sets busy until the promise settles and returns its result", async () => {
  const { result } = renderHook(() => useBusy());
  let resolve!: (v: string) => void;
  const fn = vi.fn(() => new Promise<string>((r) => { resolve = r; }));
  let p!: Promise<string | undefined>;
  act(() => { p = result.current.run(fn); });
  expect(result.current.busy).toBe(true);
  await act(async () => { resolve("done"); await p; });
  expect(await p).toBe("done");
  expect(result.current.busy).toBe(false);
});

test("run ignores calls while one is running", async () => {
  const { result } = renderHook(() => useBusy());
  let resolve!: () => void;
  const fn = vi.fn(() => new Promise<void>((r) => { resolve = r; }));
  let p!: Promise<unknown>;
  act(() => { p = result.current.run(fn); });
  await act(async () => { await result.current.run(fn); });
  expect(fn).toHaveBeenCalledTimes(1);
  await act(async () => { resolve(); await p; });
  expect(result.current.busy).toBe(false);
});

test("busy clears when the function rejects, and the rejection reaches the caller", async () => {
  const { result } = renderHook(() => useBusy());
  await act(async () => {
    await expect(result.current.run(() => Promise.reject(new Error("no")))).rejects.toThrow("no");
  });
  expect(result.current.busy).toBe(false);
});
