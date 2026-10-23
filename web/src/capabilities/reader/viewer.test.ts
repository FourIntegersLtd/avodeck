// The page viewer's arithmetic: quarter turns, where a box on the page lands once the page is turned,
// how big the page is drawn for each fit, the page strip's window, and the per-viewer view settings.
import { afterEach, describe, expect, test, vi } from "vitest";
import { clampZoom, drawnSize, loadView, saveView, stripWindow, turnBy, turnedBox, VIEW_DEFAULTS } from "./viewer";

describe("quarter turns", () => {
  test.each([
    [0, 90, 90],
    [0, -90, 270],
    [270, 90, 0],
    [90, 180, 270],
    [180, 180, 0],
  ])("%i turned by %i is %i", (from, by, to) => expect(turnBy(from, by)).toBe(to));
});

describe("a box on the page after a turn (fractions of the turned page)", () => {
  const b = { x0: 0.1, y0: 0.2, x1: 0.5, y1: 0.3 };
  test("upright it stays where it is", () => expect(turnedBox(b, 0)).toEqual(b));
  test("a quarter turn right moves the top edge to the right side", () =>
    expect(turnedBox(b, 90)).toEqual({ x0: 0.7, y0: 0.1, x1: 0.8, y1: 0.5 }));
  test("upside down it moves to the opposite corner", () =>
    expect(turnedBox(b, 180)).toEqual({ x0: 0.5, y0: 0.7, x1: 0.9, y1: 0.8 }));
  test("a quarter turn left moves the top edge to the left side", () =>
    expect(turnedBox(b, 270)).toEqual({ x0: 0.2, y0: 0.5, x1: 0.3, y1: 0.9 }));
  test("four quarter turns bring it back", () => {
    let box = b;
    for (let i = 0; i < 4; i++) box = turnedBox(box, 90);
    for (const k of ["x0", "y0", "x1", "y1"] as const) expect(box[k]).toBeCloseTo(b[k], 10);
  });
});

describe("how big the page is drawn", () => {
  // A portrait page 1000 x 1400 in a space 800 wide and 700 high.
  const page = { width: 1000, height: 1400 };
  const space = { width: 800, height: 700 };
  test("fit width fills the width", () =>
    expect(drawnSize(page, space, 0, { fit: "width", zoom: 100 })).toEqual({ width: 800, height: 1120 }));
  test("whole page fits the height too", () =>
    expect(drawnSize(page, space, 0, { fit: "page", zoom: 100 })).toEqual({ width: 500, height: 700 }));
  test("turned a quarter the page is landscape and fit width uses its long side", () =>
    expect(drawnSize(page, space, 90, { fit: "width", zoom: 100 })).toEqual({ width: 800, height: 800 / 1.4 }));
  test("a zoom is a share of the width", () =>
    expect(drawnSize(page, space, 0, { fit: "zoom", zoom: 150 })).toEqual({ width: 1200, height: 1680 }));
  test("zoom stays between 50% and 400%", () => {
    expect(clampZoom(10)).toBe(50);
    expect(clampZoom(1000)).toBe(400);
    expect(clampZoom(125)).toBe(125);
  });
});

describe("the page strip draws only the pages in view", () => {
  test("4,000 pages, 10 in view, draws about a dozen", () => {
    const w = stripWindow({ count: 4000, itemSize: 100, scroll: 50_000, view: 1000, overscan: 2 });
    expect(w).toEqual({ start: 498, end: 512 });
  });
  test("never past the ends", () => {
    expect(stripWindow({ count: 5, itemSize: 100, scroll: 0, view: 1000, overscan: 2 })).toEqual({ start: 0, end: 5 });
    expect(stripWindow({ count: 4000, itemSize: 100, scroll: 399_900, view: 1000, overscan: 2 }).end).toBe(4000);
  });
});

describe("view settings belong to this viewer and survive a page change", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });
  test("saved and loaded", () => {
    saveView({ ...VIEW_DEFAULTS, fit: "zoom", zoom: 175, brightness: 120, contrast: 140 });
    expect(loadView()).toEqual({ fit: "zoom", zoom: 175, brightness: 120, contrast: 140 });
  });
  test("nonsense in storage falls back to the defaults", () => {
    localStorage.setItem("avodeck.reader.view", '{"fit":"sideways","zoom":"big"}');
    expect(loadView()).toEqual(VIEW_DEFAULTS);
  });
  test("when storage is blocked the settings still hold for this visit", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => saveView({ ...VIEW_DEFAULTS, zoom: 200, fit: "zoom" })).not.toThrow();
    expect(loadView()).toEqual({ ...VIEW_DEFAULTS, zoom: 200, fit: "zoom" });
  });
});
