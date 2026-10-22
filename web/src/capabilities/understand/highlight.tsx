// S11 question highlight: pointing at a question's chip lights that question's entries everywhere on the
// screen. The highlight stream (docs/design/timeline-v2-followups.md, item 2) fills this in.
import { createContext, useContext, type ReactNode } from "react";

interface Highlight {
  questionId: string | null;
  show: (questionId: string | null) => void;
}

const HighlightContext = createContext<Highlight>({ questionId: null, show: () => undefined });

export function HighlightProvider({ children }: { children: ReactNode }) {
  return <HighlightContext.Provider value={{ questionId: null, show: () => undefined }}>{children}</HighlightContext.Provider>;
}

export function useHighlight(): Highlight {
  return useContext(HighlightContext);
}

/** Attributes for anything that belongs to some questions (a card, a pin, a list row). */
export function useHighlightAttrs(questionIds: readonly string[]): Record<string, string> {
  void questionIds;
  return {};
}
