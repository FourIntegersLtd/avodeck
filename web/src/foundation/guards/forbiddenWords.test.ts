// @vitest-environment node
// Rule 11: no country, case-type or specialty word in web code or copy. Words such as the party
// names or the clinician's title come from the pack vocabulary. The list is packs/schema/lint_words.yaml.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parse } from "yaml";
import { expect, test } from "vitest";
import { files, LEGAL_AGREEMENT_FILES, REPO, WEB } from "./walk";

interface LintWords {
  slice_code_forbidden: string[];
  country_words: string[];
  specialty_words: string[];
}

const lint = parse(readFileSync(join(REPO, "packs/schema/lint_words.yaml"), "utf8")) as LintWords;
const WORDS = [...new Set([...lint.slice_code_forbidden, ...lint.country_words, ...lint.specialty_words])];

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
// All-capital words match case-sensitively (so "gpu" and "nice" are fine), others in any case.
const PATTERNS = WORDS.map((w) => ({ word: w, re: new RegExp(`(?<![A-Za-z0-9_])${escape(w)}(?![A-Za-z0-9_])`, w === w.toUpperCase() ? "" : "i") }));

export function forbiddenIn(text: string): string[] {
  return PATTERNS.filter(({ re }) => re.test(text)).map(({ word }) => word);
}

const SCANNED = /^src\/(capabilities|app|foundation\/(ui|i18n|lib))\//;

test("the list holds the words in architecture 3.5", () => {
  for (const w of ["CPR", "PD35", "Part 35", "NICE", "statement of truth", "claimant", "defendant", "solicitor", "GP", "EMIS", "SystmOne", "NHS number"]) {
    expect(WORDS).toContain(w);
  }
});

test("the scan finds planted words and ignores lookalikes", () => {
  expect(forbiddenIn("Ask the Solicitor")).toEqual(["solicitor"]);
  expect(forbiddenIn("As the GP saw it")).toContain("GP");
  expect(forbiddenIn("gpu nice hemisphere claimants_x")).toEqual([]);
});

test("no web capability, app code, design-system component or copy contains a forbidden word", () => {
  const scanned = files(join(WEB, "src"), (rel) => SCANNED.test(rel) && !LEGAL_AGREEMENT_FILES.has(rel) && /\.(ts|tsx|json|css)$/.test(rel));
  expect(scanned.some((p) => p.endsWith("en-GB.json"))).toBe(true);
  const findings = scanned.flatMap((p) => forbiddenIn(readFileSync(p, "utf8")).map((w) => `${p.slice(WEB.length + 1)}: ${w}`));
  expect(findings).toEqual([]);
});
