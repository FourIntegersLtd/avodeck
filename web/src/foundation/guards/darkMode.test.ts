// Dark mode guard: every colour token has a dark value in both dark blocks, no colour literals
// bypass the tokens, and the main text pairs meet WCAG AA in both themes.
import { readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";
import { files, WEB } from "./walk";

const css = readFileSync(join(WEB, "src/foundation/ui/tokens.css"), "utf8");
const HEX = /^#[0-9a-fA-F]{3,8}$/;

function declarations(block: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of block.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}

function blockAfter(marker: string): string {
  const start = css.indexOf(marker);
  if (start < 0) return "";
  const open = css.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    if (css[i] === "{") depth++;
    if (css[i] === "}" && --depth === 0) return css.slice(open + 1, i);
  }
  return "";
}

const light = declarations(css.slice(css.indexOf(":root {") + 7, css.indexOf("}", css.indexOf(":root {"))));
const lightColours = Object.entries(light).filter(([, v]) => HEX.test(v)).map(([k]) => k);
const MEDIA = "@media (prefers-color-scheme: dark)";
const darkMedia = declarations(blockAfter(`${MEDIA} {\n  :root:not([data-theme="light"])`));
const darkAttr = declarations(blockAfter(':root[data-theme="dark"]'));

function lum(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  const c = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
const ratio = (a: string, b: string) => {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};

describe("dark mode tokens", () => {
  it("guards the media query with :root:not([data-theme=light])", () => {
    expect(css).toContain(MEDIA);
    expect(css).toContain(':root:not([data-theme="light"])');
    expect(css).toContain(':root[data-theme="dark"]');
  });

  it("gives every light colour token a dark value in both blocks", () => {
    expect(lightColours.length).toBeGreaterThan(20);
    for (const t of lightColours) {
      expect(darkMedia[t], `${t} missing in media dark`).toBeDefined();
      expect(darkAttr[t], `${t} missing in data-theme=dark`).toBeDefined();
    }
  });

  const pairs: [string, string][] = [
    ["--ink", "--surface"], ["--ink", "--surface-soft"], ["--ink-soft", "--surface"],
    ["--ink-soft", "--surface-soft"], ["--ink", "--surface-muted"],
    ["--on-accent", "--green"], ["--on-brand", "--green-dark"],
    ["--green", "--surface"], ["--amber", "--amber-tint"], ["--red", "--red-tint"],
    ["--blue", "--blue-tint"], ["--green-dark", "--green-tint"], ["--red", "--surface"],
  ];
  const themes: [string, Record<string, string>][] = [
    ["light", light],
    ["dark (media)", { ...light, ...darkMedia }],
    ["dark (attribute)", { ...light, ...darkAttr }],
  ];
  for (const [name, theme] of themes) {
    it(`meets AA contrast in ${name}`, () => {
      for (const [fg, bg] of pairs) {
        expect(theme[fg], fg).toMatch(HEX);
        expect(ratio(theme[fg], theme[bg]), `${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
      }
    });
  }
});

// Files that legitimately keep literals: artwork and test data, and pack fixtures.
const ALLOW = [
  "src/foundation/ui/tokens.css", "src/foundation/ui/Logo.tsx", "src/capabilities/trial/Lookup.tsx",
  "src/capabilities/after-serving/icons.tsx", "src/capabilities/pipeline/BundleReady.tsx",
  "src/foundation/platform-client/pack-fixtures.json",
];
const LITERAL = /#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/;

describe("no colour literals outside the tokens", () => {
  it("uses tokens everywhere (shadows, overlays and the QR white are exempt)", () => {
    const bad: string[] = [];
    const list = files(
      join(WEB, "src"),
      (rel) => /\.(css|tsx?|json)$/.test(rel) && !/\.(test|spec)\./.test(rel) && !/testing\.ts$/.test(rel) && !rel.startsWith("src/test/"),
    );
    for (const f of list) {
      const rel = relative(WEB, f).split("\\").join("/");
      if (ALLOW.includes(rel)) continue;
      readFileSync(f, "utf8").split("\n").forEach((line, i) => {
        if (!LITERAL.test(line)) return;
        if (/shadow|backdrop|\.av-qr\b/.test(line)) return;
        bad.push(`${rel}:${i + 1}`);
      });
    }
    expect(bad).toEqual([]);
  });
});
