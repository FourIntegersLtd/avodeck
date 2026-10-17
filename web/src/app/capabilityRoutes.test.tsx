import type { RouteObject } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { capabilityRoutes, routesFrom } from "./capabilityRoutes";
import { routes } from "./routes";

const paths = (list: RouteObject[]) => list.map((r) => r.path);

function signedInChildren(): RouteObject[] {
  const root = routes[0]?.children ?? [];
  const guarded = root.find((r) => r.path === undefined && !r.index && r.children);
  return guarded?.children ?? [];
}

describe("capability routes", () => {
  it("collects every array exported as <name>Routes from each capability's index, in a stable order", () => {
    const found = routesFrom({
      "../capabilities/today/index.tsx": { todayRoutes: [{ path: "today" }] },
      "../capabilities/close/index.tsx": { closeRoutes: [{ path: "cases/:caseId/close" }], helper: [{ path: "no" }] },
      "../capabilities/plain/index.tsx": { somethingElse: 1, brokenRoutes: "not a list" },
    });
    expect(paths(found)).toEqual(["cases/:caseId/close", "today"]);
  });

  it("mounts the capabilities on this branch without anyone editing routes.tsx", () => {
    expect(paths(capabilityRoutes)).toEqual(expect.arrayContaining(["today", "account", "messages"]));
  });

  it("puts every capability route behind the signed-in guard, before the case layout", () => {
    const children = signedInChildren();
    expect(paths(children)).toEqual([...paths(capabilityRoutes), "cases/:caseId"]);
  });
});
