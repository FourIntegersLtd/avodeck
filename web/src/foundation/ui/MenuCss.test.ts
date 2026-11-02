// The menu is portalled to <body>, so its CSS must put it above the reader's full screen and keep rows big.
import { readFileSync } from "node:fs";
import { expect, test } from "vitest";

const css = readFileSync("src/foundation/ui/styles.css", "utf8");
const reader = readFileSync("src/capabilities/reader/reader.css", "utf8");
const rule = (selector: string) => new RegExp(`(?:^|\\n)${selector.replace(/[.[\]()>:*]/g, "\\$&")} \\{([^}]*)\\}`).exec(css)?.[1] ?? "";

test("the menu sits above the reader's full screen", () => {
  const full = Number(/\.rd\.rd--full \{[^}]*z-index:\s*(\d+)/.exec(reader)?.[1]);
  const menu = Number(/z-index:\s*(\d+)/.exec(rule(".av-menu"))?.[1]);
  expect(full).toBeGreaterThan(0);
  expect(menu).toBeGreaterThan(full);
});

test("every menu row is at least 52px tall on a desktop too", () => {
  expect(Number(/min-height:\s*(\d+)px/.exec(rule(".av-menu__item"))?.[1])).toBeGreaterThanOrEqual(52);
});

test("on a phone the popper wrapper becomes a full-width bottom sheet", () => {
  const phone = /@media \(max-width: 900px\) \{([\s\S]*?)\n\}/.exec(css.slice(css.indexOf(".av-menu__shortcut")))?.[1] ?? "";
  const wrapper = /\[data-radix-popper-content-wrapper\]:has\(> \.av-menu\) \{([^}]*)\}/.exec(phone)?.[1] ?? "";
  expect(wrapper).toMatch(/inset:\s*auto 0 0 0\s*!important/);
  expect(wrapper).toMatch(/transform:\s*none\s*!important/);
  expect(wrapper).toMatch(/width:\s*100%/);
});
