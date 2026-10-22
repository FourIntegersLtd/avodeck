import { readdirSync, existsSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { capabilityModules } from "./capabilityModules";

describe("capabilityModules", () => {
  it("lists every capability folder that has an index", () => {
    const root = join(__dirname, "../capabilities");
    const folders = readdirSync(root, { withFileTypes: true })
      .filter((entry) => entry.isDirectory() && existsSync(join(root, entry.name, "index.tsx")))
      .map((entry) => entry.name)
      .sort();
    expect(Object.keys(capabilityModules).sort()).toEqual(folders);
  });
});
