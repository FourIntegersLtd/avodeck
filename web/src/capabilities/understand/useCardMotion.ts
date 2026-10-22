// S11 smooth zoom: when the window changes, each card slides from where it was to its new place (measured,
// inverted, then let go: "FLIP"), and cards that arrive fade in, so the eye can follow. Redraws for any other
// reason (a resize, a selection) move nothing, and nothing moves for people who ask for less motion.
import { useLayoutEffect, useRef, type RefObject } from "react";
import { moves, type Spot } from "./motion";
import "./motion.css";

const SLIDE = "transform 250ms ease";
/** The new window's entries can arrive a moment after the window changes; keep animating until then. */
const SETTLE_MS = 1500;

function lessMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function keyOf(card: HTMLElement): string | null {
  return card.dataset.facts?.split(" ")[0] ?? null;
}

function measure(board: HTMLElement): Map<string, { spot: Spot; card: HTMLElement }> {
  const origin = board.getBoundingClientRect();
  const out = new Map<string, { spot: Spot; card: HTMLElement }>();
  for (const card of board.querySelectorAll<HTMLElement>(".av-story-card")) {
    const key = keyOf(card);
    if (!key) continue;
    const box = card.getBoundingClientRect();
    out.set(key, { spot: { x: box.left - origin.left, y: box.top - origin.top }, card });
  }
  return out;
}

export function useCardMotion(board: RefObject<HTMLElement>, windowKey: string): void {
  const last = useRef<{ key: string; spots: Map<string, Spot>; changedAt: number } | null>(null);

  useLayoutEffect(() => {
    const el = board.current;
    if (!el) return;
    const now = measure(el);
    const spots = new Map([...now].map(([key, v]) => [key, v.spot]));
    const before = last.current;
    const changedAt = before && before.key !== windowKey ? Date.now() : (before?.changedAt ?? 0);
    last.current = { key: windowKey, spots, changedAt };
    if (!before || Date.now() - changedAt > SETTLE_MS || lessMotion()) return;

    const plan = moves(before.spots, spots);
    if (plan.moved.length === 0 && plan.entered.length === 0) return;
    for (const { key, dx, dy } of plan.moved) {
      const card = now.get(key)!.card;
      card.style.transition = "none";
      card.style.transform = `translate(${dx}px, ${dy}px)`;
    }
    for (const key of plan.entered) now.get(key)!.card.classList.add("av-motion-enter");
    const frame = requestAnimationFrame(() => {
      for (const { key } of plan.moved) {
        const card = now.get(key)!.card;
        card.style.transition = SLIDE;
        card.style.transform = "";
        card.addEventListener("transitionend", () => (card.style.transition = ""), { once: true });
      }
      for (const key of plan.entered) {
        const card = now.get(key)!.card;
        card.addEventListener("animationend", () => card.classList.remove("av-motion-enter"), { once: true });
      }
    });
    return () => cancelAnimationFrame(frame);
  });
}
