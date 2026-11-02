import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const read = (p: string) => readFileSync(resolve(__dirname, p), "utf8");

describe("phone-safe heights", () => {
  it("viewport covers the notch area", () => {
    expect(read("../../../index.html")).toContain("viewport-fit=cover");
  });
  it("defines --safe-bottom", () => {
    expect(read("./tokens.css")).toContain("--safe-bottom: env(safe-area-inset-bottom");
  });
  it("every 100vh in styles.css is followed by a 100dvh line", () => {
    const lines = read("./styles.css").split("\n");
    const hits = lines.map((l, i) => (l.includes("100vh") ? i : -1)).filter((i) => i >= 0);
    expect(hits.length).toBeGreaterThan(0);
    for (const i of hits) {
      expect(lines[i + 1] ?? "").toContain("100dvh");
    }
  });
});
