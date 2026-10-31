// Screens hand an upload an abort signal that fires when they go away, and show its phase in plain words.
import { act, renderHook } from "@testing-library/react";
import { expect, test } from "vitest";
import { t } from "../i18n";
import { useUpload } from "./useUpload";

test("the signal is aborted when the screen goes away", () => {
  const { result, unmount } = renderHook(() => useUpload());
  const { signal } = result.current.options();
  expect(signal?.aborted).toBe(false);
  unmount();
  expect(signal?.aborted).toBe(true);
});

test("the phase shows in plain words while it is running, and clears when it ends", () => {
  const { result } = renderHook(() => useUpload());
  expect(result.current.status).toBeNull();
  act(() => result.current.options().onProgress?.({ phase: "retrying", fraction: 0.3 }));
  expect(result.current.status).toBe(t("upload.phase.retrying"));
  act(() => result.current.options().onProgress?.({ phase: "done", fraction: 1 }));
  expect(result.current.status).toBeNull();
});
