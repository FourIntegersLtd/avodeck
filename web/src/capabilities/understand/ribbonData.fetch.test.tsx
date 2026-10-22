// The ribbon's data comes from the overview endpoint, with the day only when one is chosen.
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { useRibbonData } from "./ribbonData";
import { overview } from "./testing";

function serveOverview() {
  const seen: URL[] = [];
  server.use(
    http.get("/v1/cases/:caseId/timeline/overview", ({ request }) => {
      seen.push(new URL(request.url));
      return json(overview());
    }),
  );
  return seen;
}

describe("useRibbonData", () => {
  test("asks the overview endpoint and returns it as it is", async () => {
    const seen = serveOverview();
    const { result } = renderHook(() => useRibbonData(CASE_A, ""));
    await waitFor(() => expect(result.current.data).toEqual(overview()));
    expect(seen[0]!.searchParams.has("as_seen_on")).toBe(false);
  });

  test("passes the day when one is chosen", async () => {
    const seen = serveOverview();
    renderHook(() => useRibbonData(CASE_A, "2024-03-01"));
    await waitFor(() => expect(seen.length).toBeGreaterThan(0));
    expect(seen[0]!.searchParams.get("as_seen_on")).toBe("2024-03-01");
  });
});
