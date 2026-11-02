import { describe, expect, it } from "vitest";
import { capabilityOfflineRoutes } from "../../app/capabilityRoutes";

describe("the witness-box route is in the main bundle (rule 4, no service worker)", () => {
  it("has an element and no lazy loader", () => {
    const route = capabilityOfflineRoutes.find((r) => r.path === "cases/:caseId/witness-box");
    expect(route).toBeDefined();
    expect(route?.element).toBeTruthy();
    expect(route?.lazy).toBeUndefined();
  });
});
