// S11 smooth zoom on the board: after the window changes, a card that moved starts from where it was and slides
// to its new place; nothing moves when the person has asked for less motion.
import { render } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { useCardMotion } from "./useCardMotion";

function Board({ windowKey, left }: { windowKey: string; left: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useCardMotion(ref, windowKey);
  return (
    <div ref={ref}>
      <button type="button" className="av-story-card" data-facts="f1 f2" data-left={left}>
        card
      </button>
    </div>
  );
}

function placeCards() {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const left = Number((this as HTMLElement).dataset.left ?? 0);
    return { x: left, y: 0, left, top: 0, right: left + 10, bottom: 10, width: 10, height: 10, toJSON: () => ({}) } as DOMRect;
  });
}

function prefersLessMotion(less: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: less && query.includes("reduce"), media: query, addEventListener: () => undefined, removeEventListener: () => undefined }));
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("useCardMotion", () => {
  test("a card that moved when the window changed starts from its old place", () => {
    placeCards();
    prefersLessMotion(false);
    const { container, rerender } = render(<Board windowKey="2024-02-01|2024-08-31" left={300} />);
    rerender(<Board windowKey="2024-03-01|2024-08-31" left={100} />);
    const card = container.querySelector<HTMLElement>(".av-story-card")!;
    expect(card.style.transform).toBe("translate(200px, 0px)");
  });

  test("a redraw with the same window moves nothing", () => {
    placeCards();
    prefersLessMotion(false);
    const { container, rerender } = render(<Board windowKey="w1" left={300} />);
    rerender(<Board windowKey="w1" left={300} />);
    expect(container.querySelector<HTMLElement>(".av-story-card")!.style.transform).toBe("");
  });

  test("nothing slides when the person prefers less motion", () => {
    placeCards();
    prefersLessMotion(true);
    const { container, rerender } = render(<Board windowKey="w1" left={300} />);
    rerender(<Board windowKey="w2" left={100} />);
    expect(container.querySelector<HTMLElement>(".av-story-card")!.style.transform).toBe("");
  });

  test("a card that arrives with the new window fades in", () => {
    placeCards();
    prefersLessMotion(false);
    function Two({ windowKey, both }: { windowKey: string; both: boolean }) {
      const ref = useRef<HTMLDivElement>(null);
      useCardMotion(ref, windowKey);
      return (
        <div ref={ref}>
          <button type="button" className="av-story-card" data-facts="f1" data-left={0}>a</button>
          {both && <button type="button" className="av-story-card" data-facts="f9" data-left={50}>b</button>}
        </div>
      );
    }
    const { container, rerender } = render(<Two windowKey="w1" both={false} />);
    rerender(<Two windowKey="w2" both />);
    expect(container.querySelector('[data-facts="f9"]')!.classList.contains("av-motion-enter")).toBe(true);
  });
});
