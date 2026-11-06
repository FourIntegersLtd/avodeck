// The browser's own confirm(), alert() and prompt() boxes are never used: the app has one confirmation
// box (`useConfirm`, foundation/lib/confirm.tsx) with Confirm and Cancel, so every question looks the same.
import { readFileSync } from "node:fs";
import { expect, test } from "vitest";
import { files, isTest, WEB } from "./walk";

const BROWSER_DIALOG = /(?<![\w.$])(?:window\.|globalThis\.)(?:confirm|alert|prompt)\s*\(|(?<![\w.$])(?:alert|prompt)\s*\(|(?<![\w.$])confirm\s*\(\s*(?:w|t)\(/;

const withoutComments = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

test("no source file opens a browser confirm, alert or prompt box", () => {
  const hits = files(`${WEB}/src`, (rel) => /\.tsx?$/.test(rel) && !isTest(rel))
    .filter((file) => BROWSER_DIALOG.test(withoutComments(readFileSync(file, "utf8"))))
    .map((file) => file.slice(WEB.length + 1));
  expect(hits).toEqual([]);
});
