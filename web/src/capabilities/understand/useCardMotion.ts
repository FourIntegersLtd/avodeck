// S11 smooth zoom: when the window changes, cards slide from their old place to the new one. The motion
// stream (docs/design/timeline-v2-followups.md, item 3) fills this in; until then the board just redraws.
import type { RefObject } from "react";

export function useCardMotion(board: RefObject<HTMLElement>, windowKey: string): void {
  void board;
  void windowKey;
}
