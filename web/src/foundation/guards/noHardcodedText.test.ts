// @vitest-environment node
// Every user-facing string comes from an i18n key (AGENTS.md rule 11, web/AGENTS.md "Copy").
// Scans every component for JSX text, string-literal children and text attributes.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import ts from "typescript";
import { expect, test } from "vitest";
import { files, isTest, WEB } from "./walk";

const TEXT_ATTRIBUTES = new Set(["aria-label", "aria-description", "title", "placeholder", "alt", "label"]);
const HAS_WORDS = /[A-Za-z]{2,}/;

export function hardcodedText(source: string, fileName = "x.tsx"): string[] {
  const file = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const found: string[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isJsxText(node) && HAS_WORDS.test(node.text)) found.push(node.text.trim());
    if (ts.isJsxExpression(node) && node.expression && ts.isJsxElement(node.parent) || ts.isJsxExpression(node) && node.parent && ts.isJsxFragment(node.parent)) {
      const e = node.expression;
      if (e && (ts.isStringLiteral(e) || ts.isNoSubstitutionTemplateLiteral(e)) && HAS_WORDS.test(e.text)) found.push(e.text);
    }
    if (ts.isJsxAttribute(node) && TEXT_ATTRIBUTES.has(node.name.getText()) && node.initializer) {
      const init = node.initializer;
      const literal = ts.isStringLiteral(init)
        ? init
        : ts.isJsxExpression(init) && init.expression && ts.isStringLiteral(init.expression)
          ? init.expression
          : undefined;
      if (literal && HAS_WORDS.test(literal.text)) found.push(`${node.name.getText()}="${literal.text}"`);
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return found;
}

test("the scan finds planted text and ignores keys and class names", () => {
  expect(hardcodedText(`const A = () => <p>Hello there</p>;`)).toEqual(["Hello there"]);
  expect(hardcodedText(`const A = () => <p>{"Sign in"}</p>;`)).toEqual(["Sign in"]);
  expect(hardcodedText(`const A = () => <img alt="A picture" />;`)).toEqual(['alt="A picture"']);
  expect(hardcodedText(`const A = () => <button aria-label={"Close"} />;`)).toEqual(['aria-label="Close"']);
  expect(hardcodedText(`const A = () => <div className="card wide" data-x="y">{t("app.name")} · 2</div>;`)).toEqual([]);
});

test("no component contains hard-coded user-facing text", () => {
  const components = files(join(WEB, "src"), (rel) => rel.endsWith(".tsx") && !isTest(rel) && !rel.startsWith("src/foundation/guards/"));
  expect(components.length).toBeGreaterThan(5);
  const findings = components.flatMap((p) => hardcodedText(readFileSync(p, "utf8"), p).map((text) => `${p.slice(WEB.length + 1)}: ${text}`));
  expect(findings).toEqual([]);
});
