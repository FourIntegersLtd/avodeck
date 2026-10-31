// A person can stop an upload that is running: the signal fires, and the next upload starts afresh.
import { act, renderHook } from "@testing-library/react";
import { expect, test } from "vitest";
import { useUpload } from "./useUpload";

test("stop aborts the running upload, says it was stopped, and the next options carry a fresh signal", () => {
  const { result } = renderHook(() => useUpload());
  const first = result.current.options().signal!;
  act(() => result.current.stop());
  expect(first.aborted).toBe(true);
  expect(result.current.stopped()).toBe(true);
  act(() => result.current.begin());
  expect(result.current.stopped()).toBe(false);
  expect(result.current.options().signal!.aborted).toBe(false);
});
