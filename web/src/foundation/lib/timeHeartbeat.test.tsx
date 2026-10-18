// Quiet time-keeping on every case screen: the case frame mounts it for any screen a pack step holds.
import { act, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { useTimeHeartbeat } from "./timeHeartbeat";

describe("the time heartbeat", () => {
  afterEach(() => vi.useRealTimers());

  function Beat() {
    useTimeHeartbeat(CASE_A, "report");
    return null;
  }

  test("posts the screen, whether the tab is visible and the seconds since the last input every 30 seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const bodies: unknown[] = [];
    server.use(http.post("/v1/cases/:caseId/time/heartbeat", async ({ request }) => (bodies.push(await request.json()), json({ counted: true, stage: "write" }))));
    render(<Beat />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ screen: "report", visible: true, idle_seconds: 30 });
    document.dispatchEvent(new Event("keydown"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1]).toEqual({ screen: "report", visible: true, idle_seconds: 30 });
  });

  test("a refused beat is ignored quietly", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let calls = 0;
    server.use(http.post("/v1/cases/:caseId/time/heartbeat", () => (calls++, apiError(400, "invalid"))));
    render(<Beat />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    await waitFor(() => expect(calls).toBe(1));
  });
});
