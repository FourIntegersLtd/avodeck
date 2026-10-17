// @vitest-environment node
// Every key the code asks for exists in the base language file, and no value is empty.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "vitest";
import { files, isTest, WEB } from "./walk";

const BASE = join(WEB, "src/foundation/i18n/en-GB.json");
const KEY_CALL = /\bt\(\s*["'`]([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)["'`]/g;

test("en-GB.json is a flat map of dotted keys to non-empty strings", () => {
  const messages = JSON.parse(readFileSync(BASE, "utf8")) as Record<string, unknown>;
  expect(Object.keys(messages).length).toBeGreaterThan(20);
  for (const [key, value] of Object.entries(messages)) {
    expect(key).toMatch(/^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$/);
    expect(typeof value === "string" && value.trim().length > 0, key).toBe(true);
  }
});

test("every key used in the code exists (or has plural forms)", () => {
  const messages = JSON.parse(readFileSync(BASE, "utf8")) as Record<string, string>;
  const src = files(join(WEB, "src"), (rel) => /\.(ts|tsx)$/.test(rel) && !isTest(rel) && !rel.startsWith("src/foundation/guards/"));
  const missing: string[] = [];
  for (const path of src) {
    for (const [, key] of readFileSync(path, "utf8").matchAll(KEY_CALL)) {
      if (!(key! in messages) && !(`${key}_one` in messages && `${key}_other` in messages)) missing.push(`${path.slice(WEB.length + 1)}: ${key}`);
    }
  }
  expect(missing).toEqual([]);
});
