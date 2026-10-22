// S11 question highlight: pointing at a question's chip lights that question's entries everywhere on the
// screen (cards, ribbon pins, list rows) and fades the rest. A polite live region says so for screen readers.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import "./highlight.css";
import { w } from "./highlight.words";

interface Highlight {
  questionId: string | null;
  /** Light up a question (with its number, for the announcement), or clear with `null`. */
  show: (questionId: string | null, n?: number) => void;
}

const HighlightContext = createContext<Highlight>({ questionId: null, show: () => undefined });

export function HighlightProvider({ children }: { children: ReactNode }) {
  const [on, setOn] = useState<{ id: string; n?: number } | null>(null);
  const show = useCallback((id: string | null, n?: number) => setOn(id === null ? null : { id, n }), []);
  const value = useMemo(() => ({ questionId: on?.id ?? null, show }), [on, show]);

  useEffect(() => {
    if (!on) return;
    const clear = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOn(null);
    };
    document.addEventListener("keydown", clear);
    return () => document.removeEventListener("keydown", clear);
  }, [on]);

  return (
    <HighlightContext.Provider value={value}>
      {children}
      <div className="av-visually-hidden" aria-live="polite" role="status">
        {on && on.n !== undefined ? w("highlight.announce", { n: on.n }) : ""}
      </div>
    </HighlightContext.Provider>
  );
}

export function useHighlight(): Highlight {
  return useContext(HighlightContext);
}

/** Attributes for anything that belongs to some questions (a card, a pin, a list row). */
export function useHighlightAttrs(questionIds: readonly string[]): Record<string, string> {
  const { questionId } = useHighlight();
  if (questionId === null) return {};
  return questionIds.includes(questionId) ? { "data-highlight": "on" } : { "data-faded": "on" };
}
