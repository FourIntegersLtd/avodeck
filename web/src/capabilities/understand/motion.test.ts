// S11 smooth zoom: which cards move, arrive or leave when the window changes (positions keyed by entry).
import { describe, expect, test } from "vitest";
import { moves } from "./motion";

describe("moves", () => {
  test("cards in both pictures move by the difference, new ones arrive and missing ones leave", () => {
    const before = new Map([
      ["a", { x: 100, y: 0 }],
      ["b", { x: 300, y: 60 }],
      ["gone", { x: 10, y: 0 }],
    ]);
    const after = new Map([
      ["a", { x: 40, y: 0 }],
      ["b", { x: 300, y: 60 }],
      ["new", { x: 500, y: 0 }],
    ]);
    expect(moves(before, after)).toEqual({ moved: [{ key: "a", dx: 60, dy: 0 }], entered: ["new"], left: ["gone"] });
  });

  test("a shift of less than a pixel is not a move", () => {
    expect(moves(new Map([["a", { x: 10, y: 0 }]]), new Map([["a", { x: 10.4, y: 0.3 }]])).moved).toEqual([]);
  });
});
