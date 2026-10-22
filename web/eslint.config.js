import js from "@eslint/js";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

// Rule 4 and web/AGENTS.md: no tokens in browser storage; only the generated client talks to the API.
const RESTRICTED_GLOBALS = [
  { name: "localStorage", message: "Never store anything sensitive in the browser (web/AGENTS.md)." },
  { name: "sessionStorage", message: "Never store anything sensitive in the browser (web/AGENTS.md)." },
  { name: "indexedDB", message: "Only the offline witness-box package may store data on the device." },
  { name: "fetch", message: "Call the API through src/foundation/platform-client only." },
];
// The one exception (screen 25): the sealed witness-box copy's own module may use IndexedDB, nothing else.
const WITNESS_BOX_STORE = "src/capabilities/trial/witnessBoxStore.ts";

// Imports go one way only: app -> capabilities -> foundation (web/AGENTS.md).
const layer = (from, banned) => ({
  files: [`src/${from}/**/*.{ts,tsx}`],
  ignores: ["src/**/*.test.{ts,tsx}"],
  rules: {
    "no-restricted-imports": ["error", { patterns: banned.map((b) => ({ group: [`**/${b}/**`, `**/${b}`], message: `${from} must not import ${b}` })) }],
  },
});

export default tseslint.config(
  { ignores: ["dist", ".next", "next-env.d.ts", "coverage", "playwright-report", "test-results", "design", "scripts/design-diff/out", "src/foundation/platform-client/schema.ts"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
    plugins: { react, "react-hooks": reactHooks },
    settings: { react: { version: "18.3" } },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Threat model T13: record text is rendered as text, never as HTML.
      "react/no-danger": "error",
      "no-restricted-globals": ["error", ...RESTRICTED_GLOBALS],
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
  {
    files: ["src/foundation/platform-client/**/*.{ts,tsx}", "src/**/*.test.{ts,tsx}", "src/test/**", "e2e/**", "*.config.ts"],
    rules: { "no-restricted-globals": "off" },
  },
  {
    // Dev tooling that drives a browser (scripts/design-diff): runs in Node, and its page callbacks in Chromium.
    files: ["scripts/**/*.mjs"],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
    rules: { "no-restricted-globals": "off" },
  },
  {
    files: [WITNESS_BOX_STORE],
    rules: { "no-restricted-globals": ["error", ...RESTRICTED_GLOBALS.filter((g) => g.name !== "indexedDB")] },
  },
  layer("foundation", ["capabilities", "app"]),
  layer("capabilities", ["app"]),
);
