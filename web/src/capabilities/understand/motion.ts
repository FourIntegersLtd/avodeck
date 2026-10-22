// S11 smooth zoom, the arithmetic: given where each card was and where it is now (keyed by entry), which
// cards slide and by how much, which arrive and which leave. No browser here.
export type Spot = { x: number; y: number };

export interface Moves {
  moved: { key: string; dx: number; dy: number }[];
  entered: string[];
  left: string[];
}

/** Under a pixel is not worth sliding. */
const STILL = 1;

export function moves(before: Map<string, Spot>, after: Map<string, Spot>): Moves {
  const moved: Moves["moved"] = [];
  const entered: string[] = [];
  for (const [key, now] of after) {
    const was = before.get(key);
    if (!was) {
      entered.push(key);
      continue;
    }
    const dx = was.x - now.x;
    const dy = was.y - now.y;
    if (Math.abs(dx) >= STILL || Math.abs(dy) >= STILL) moved.push({ key, dx, dy });
  }
  const left = [...before.keys()].filter((key) => !after.has(key));
  return { moved, entered, left };
}
