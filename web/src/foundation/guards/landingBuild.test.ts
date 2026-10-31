// The Vercel build copies the landing page's files out of ../site into dist (scripts/add-landing.sh).
// A file left out of that copy is served as the app's fallback page, not as the file (the email logo did
// exactly that), so every file in site/ must be named in the script, except the two that are not served.
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "vitest";

// vercel.json is configuration, not a served file. Dot files (.gitignore, and a developer's
// own .vercel or .env.local) are ignored by git and never reach the deploy.
const NOT_COPIED = new Set(["vercel.json"]);
const script = readFileSync(
  resolve(__dirname, "../../../scripts/add-landing.sh"),
  "utf8",
);
const copied = new Set(
  [...script.matchAll(/\.\.\/site\/([A-Za-z0-9._-]+)/g)].map(
    (match) => match[1],
  ),
);

test("every file in site/ is copied into the Vercel build", () => {
  const files = readdirSync(resolve(__dirname, "../../../../site")).filter(
    (name) => !NOT_COPIED.has(name) && !name.startsWith("."),
  );
  expect(files.length).toBeGreaterThan(0);
  expect(files.filter((name) => !copied.has(name))).toEqual([]);
});

test("the email logo is one of them", () => {
  expect(copied.has("email-logo.png")).toBe(true);
});
