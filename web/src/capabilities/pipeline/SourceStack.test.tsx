// The labels beside the stack never sit on top of each other, however the pages fall between sources.
import { render } from "@testing-library/react";
import { expect, test } from "vitest";
import { bandHeights, labelBlocks, SourceStack } from "./SourceStack";

const rows = [
  { key: "a", label: "Practice", pages: 287, colour: "#1E6B4E" },
  { key: "b", label: "Hospital", pages: 60, colour: "#4C6E91" },
  { key: "c", label: "Letters", pages: 188, colour: "#8C7556" },
  { key: "d", label: "Out of hours", pages: 24, colour: "#2C8C99" },
  { key: "", label: "Not sorted yet", pages: 13, colour: "#8A948E" },
];

test("label blocks never overlap and stay inside the drawing", () => {
  const blocks = labelBlocks(bandHeights(rows.map((r) => r.pages)));
  for (let i = 1; i < blocks.length; i += 1) {
    expect(blocks[i]!.top).toBeGreaterThanOrEqual(blocks[i - 1]!.top + blocks[i - 1]!.height);
  }
  expect(blocks[0]!.top).toBeGreaterThanOrEqual(0);
  const last = blocks[blocks.length - 1]!;
  expect(last.top + last.height).toBeLessThanOrEqual(440);
});

test("a tall band keeps its label centred when nothing is in the way", () => {
  const heights = bandHeights([300, 300]);
  const blocks = labelBlocks(heights);
  expect(blocks[0]!.top + blocks[0]!.height / 2).toBeCloseTo(heights[0]! / 2, 0);
});

test("every source is drawn with its label and count once", () => {
  const { container } = render(<SourceStack rows={rows} label="Sources" unit={() => "pages"} format={(n) => String(n)} />);
  const texts = Array.from(container.querySelectorAll("svg text")).map((t) => t.textContent);
  for (const r of rows) expect(texts.filter((t) => t?.startsWith(r.label))).toHaveLength(1);
  const ys = Array.from(container.querySelectorAll("svg text.pl-stack__name")).map((t) => Number(t.getAttribute("y")));
  for (let i = 1; i < ys.length; i += 1) expect(ys[i]! - ys[i - 1]!).toBeGreaterThanOrEqual(26);
});
