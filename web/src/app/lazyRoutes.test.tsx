// Each screen downloads only when it is first opened (plan step 6); the nav warms the download early.
import { fireEvent, screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { prefetchRoute, setRoutePrefetcher } from "../foundation/lib/routePrefetch";
import { http, HttpResponse, json, signedIn, server } from "../test/api";
import { sessionStatus, me } from "../test/fixtures";
import { renderAt } from "../test/render";
import { capabilityCaseRoutes, capabilityRoutes } from "./capabilityRoutes";
import { prefetchPath } from "./routes";

test("capability screens load lazily: every route has a lazy loader and no eager element", () => {
  for (const r of [...capabilityRoutes, ...capabilityCaseRoutes]) {
    expect(typeof r.lazy, String(r.path)).toBe("function");
    expect(r.element, String(r.path)).toBeUndefined();
  }
});

test("prefetchRoute hands the path to the registered prefetcher", () => {
  const spy = vi.fn();
  setRoutePrefetcher(spy);
  prefetchRoute("/today");
  expect(spy).toHaveBeenCalledWith("/today");
  setRoutePrefetcher(prefetchPath);
});

test("prefetchPath starts the download of the matched screen", async () => {
  const route = capabilityRoutes.find((r) => r.path === "today")!;
  const original = route.lazy as () => Promise<unknown>;
  const spy = vi.fn(original);
  route.lazy = spy as never;
  prefetchPath("/today");
  expect(spy).toHaveBeenCalled();
  route.lazy = original as never;
});

test("hovering, focusing or touching a nav link prefetches its screen", async () => {
  signedIn();
  const spy = vi.fn();
  setRoutePrefetcher(spy);
  renderAt("/account");
  const header = await screen.findByRole("banner");
  const link = await within(header).findByRole("link", { name: "Today" });
  fireEvent.mouseEnter(link);
  fireEvent.focus(link);
  fireEvent.touchStart(link);
  expect(spy).toHaveBeenCalledTimes(3);
  expect(spy).toHaveBeenCalledWith("/today");
  setRoutePrefetcher(prefetchPath);
});

test("the person and legal requests start together with the session request", async () => {
  signedIn();
  const seen: string[] = [];
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  server.use(
    http.get("/v1/auth/session", async () => {
      seen.push("session");
      await gate;
      return json(sessionStatus());
    }),
    http.get("/v1/me", () => {
      seen.push("me");
      return json(me());
    }),
    http.get("/v1/legal-agreements/status", () => {
      seen.push("legal");
      return HttpResponse.json({ accepted: true });
    }),
  );
  renderAt("/today");
  await vi.waitFor(() => expect(seen).toEqual(expect.arrayContaining(["session", "me", "legal"])));
  release();
});
