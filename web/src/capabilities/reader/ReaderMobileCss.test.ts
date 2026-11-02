// On a phone the full-screen reader must scroll to its last control: dynamic viewport height, own scroll,
// no sticky notes bar covering the bottom.
import { readFileSync } from "node:fs";
import { describe, expect, test } from "vitest";

const css = readFileSync("src/capabilities/reader/reader.css", "utf8");

function mobileBlocks(): string {
  const out: string[] = [];
  const re = /@media \(max-width: 900px\) \{/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(css))) {
    let depth = 1;
    let i = m.index + m[0].length;
    const start = i;
    while (depth && i < css.length) {
      if (css[i] === "{") depth++;
      if (css[i] === "}") depth--;
      i++;
    }
    out.push(css.slice(start, i - 1));
  }
  return out.join("\n");
}

describe("reader on a phone", () => {
  const mobile = mobileBlocks();
  const full = /\.rd\.rd--full\s*\{([^}]*)\}/.exec(mobile)?.[1] ?? "";

  test("full screen is dvh high and scrolls itself", () => {
    expect(full).toMatch(/height:\s*100vh;\s*height:\s*100dvh/);
    expect(full).toMatch(/overflow-y:\s*auto/);
    expect(full).toMatch(/overscroll-behavior:\s*contain/);
    expect(full).toMatch(/-webkit-overflow-scrolling:\s*touch/);
    expect(full).toMatch(/padding-bottom:\s*calc\(24px \+ var\(--safe-bottom, 0px\)\)/);
  });

  test("page view and menu caps use dvh with a vh fallback first", () => {
    expect(mobile).toMatch(/max-height:\s*55vh;\s*max-height:\s*55dvh/);
    const shared = readFileSync("src/foundation/ui/styles.css", "utf8");
    expect(shared).toMatch(/\.av-menu \{[^}]*max-height:\s*75vh;\s*max-height:\s*75dvh/);
  });

  test("notes are not sticky on a phone", () => {
    expect(mobile).toMatch(/\.rd-side > \.rd-notes\s*\{[^}]*position:\s*static/);
  });

  test("the reader's minimum height uses dvh", () => {
    expect(css).toMatch(/min-height:\s*100vh;\s*min-height:\s*100dvh/);
  });
});
