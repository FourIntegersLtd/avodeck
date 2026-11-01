import { act, renderHook } from "@testing-library/react";
import { expect, test } from "vitest";
import { clampZoom, drawnSize, turnBy, usePageView, VIEW_DEFAULTS, ZOOM_MAX, ZOOM_MIN } from "./pageView";

test("zoom is clamped and rounded", () => {
  expect(clampZoom(10)).toBe(ZOOM_MIN);
  expect(clampZoom(900)).toBe(ZOOM_MAX);
  expect(clampZoom(120.4)).toBe(120);
});

test("turns wrap in quarter turns", () => {
  expect(turnBy(270, 90)).toBe(0);
  expect(turnBy(0, -90)).toBe(270);
});

test("drawn size follows the turn and the fit", () => {
  const page = { width: 1000, height: 1400 };
  expect(drawnSize(page, { width: 500, height: 500 }, 0, { fit: "width", zoom: 100 })).toEqual({ width: 500, height: 700 });
  expect(drawnSize(page, { width: 500, height: 500 }, 90, { fit: "page", zoom: 100 }).width).toBe(500);
  expect(drawnSize(page, { width: 500, height: 500 }, 0, { fit: "zoom", zoom: 200 }).width).toBe(1000);
});

test("the hook starts at the defaults and steps zoom from what is on screen", () => {
  const { result } = renderHook(() => usePageView());
  expect(result.current.view).toEqual(VIEW_DEFAULTS);
  act(() => result.current.zoomIn(80));
  expect(result.current.view).toMatchObject({ fit: "zoom", zoom: 105 });
  act(() => result.current.zoomOut());
  expect(result.current.view.zoom).toBe(80);
  act(() => result.current.reset());
  expect(result.current.view).toEqual(VIEW_DEFAULTS);
  expect(result.current.turn).toBe(0);
});

test("turn, fit and clearer settings", () => {
  const { result } = renderHook(() => usePageView({ turn: 90 }));
  act(() => result.current.turnRight());
  expect(result.current.turn).toBe(180);
  act(() => result.current.turnLeft());
  act(() => result.current.turnLeft());
  expect(result.current.turn).toBe(0);
  act(() => result.current.setFit("page"));
  expect(result.current.view.fit).toBe("page");
  act(() => result.current.setClearer({ brightness: 120 }));
  expect(result.current.view.brightness).toBe(120);
  expect(result.current.filter).toBe("brightness(1.2) contrast(1)");
});

test("the owner is told of changes so it can keep its own copy", () => {
  const seen: number[] = [];
  const { result } = renderHook(() => usePageView({ onTurn: (t) => seen.push(t) }));
  act(() => result.current.turnRight());
  expect(seen).toEqual([90]);
});
