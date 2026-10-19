import type { RouteObject } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { capabilityCaseRoutes, capabilityOfflineRoutes, capabilityRoutes, caseRoutesFrom, offlineRoutesFrom, routesFrom } from "./capabilityRoutes";
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

  it("keeps <name>CaseRoutes out of the top level and collects them for inside the case frame", () => {
    const modules = {
      "../capabilities/setup/index.tsx": { setupRoutes: [{ path: "profile" }], setupCaseRoutes: [{ path: "questions" }] },
      "../capabilities/close/index.tsx": { closeCaseRoutes: [{ path: "close" }] },
    };
    expect(paths(routesFrom(modules))).toEqual(["profile"]);
    expect(paths(caseRoutesFrom(modules))).toEqual(["close", "questions"]);
  });

  it("renders slice case routes inside the case layout", () => {
    const caseRoute = signedInChildren().find((r) => r.path === "cases/:caseId");
    expect(paths(caseRoute?.children ?? [])).toEqual(paths(capabilityCaseRoutes));
    expect(paths(capabilityCaseRoutes)).toEqual(expect.arrayContaining(["conflicts", "questions"]));
    expect(paths(capabilityRoutes)).not.toContain("questions");
  });

  it("mounts the capabilities on this branch without anyone editing routes.tsx", () => {
    expect(paths(capabilityRoutes)).toEqual(expect.arrayContaining(["today", "account", "messages"]));
  });

  it("puts every capability route behind the signed-in guard, before the case layout", () => {
    const children = signedInChildren();
    expect(paths(children)).toEqual([...paths(capabilityRoutes), "cases/:caseId"]);
  });

  it("mounts <name>OfflineRoutes outside the signed-in guard and nowhere else", () => {
    const modules = { "../capabilities/trial/index.tsx": { trialRoutes: [{ path: "a" }], trialOfflineRoutes: [{ path: "b" }] } };
    expect(paths(routesFrom(modules))).toEqual(["a"]);
    expect(paths(offlineRoutesFrom(modules))).toEqual(["b"]);
    const root = routes[0]?.children ?? [];
    expect(paths(root)).toEqual(expect.arrayContaining(paths(capabilityOfflineRoutes)));
    for (const path of paths(capabilityOfflineRoutes)) expect(paths(signedInChildren())).not.toContain(path);
  });
});
