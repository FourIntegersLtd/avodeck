import { describe, expect, it } from "vitest";
import { capabilityOfflineRoutes } from "../../app/capabilityRoutes";
import { trialOfflineRoutes } from "./index";

describe("offline routes are in the main bundle (rule 4, no service worker)", () => {
  it("every offline route has an element and no lazy loader", () => {
    expect(capabilityOfflineRoutes.length).toBeGreaterThan(0);
    for (const route of [...capabilityOfflineRoutes, ...trialOfflineRoutes]) {
      expect(route.element).toBeTruthy();
      expect(route.lazy).toBeUndefined();
    }
  });
});
