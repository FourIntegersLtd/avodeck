import js from "@eslint/js";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

// Imports go one way only: app -> capabilities -> foundation (web/AGENTS.md).
const layer = (from, banned) => ({
  files: [`src/${from}/**/*.{ts,tsx}`],
  ignores: ["src/**/*.test.{ts,tsx}"],
  rules: {
    "no-restricted-imports": ["error", { patterns: banned.map((b) => ({ group: [`**/${b}/**`, `**/${b}`], message: `${from} must not import ${b}` })) }],
  },
});

export default tseslint.config(
  { ignores: ["dist", "coverage", "playwright-report", "test-results", "src/foundation/platform-client/schema.ts"] },
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
      // Rule 4 and web/AGENTS.md: no tokens in browser storage; only the generated client talks to the API.
      "no-restricted-globals": [
        "error",
        { name: "localStorage", message: "Never store anything sensitive in the browser (web/AGENTS.md)." },
        { name: "sessionStorage", message: "Never store anything sensitive in the browser (web/AGENTS.md)." },
        { name: "indexedDB", message: "Only the offline witness-box package may store data on the device." },
        { name: "fetch", message: "Call the API through src/foundation/platform-client only." },
      ],
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
  {
    files: ["src/foundation/platform-client/**/*.{ts,tsx}", "src/**/*.test.{ts,tsx}", "src/test/**", "e2e/**", "*.config.ts"],
    rules: { "no-restricted-globals": "off" },
  },
  layer("foundation", ["capabilities", "app"]),
  layer("capabilities", ["app"]),
);
