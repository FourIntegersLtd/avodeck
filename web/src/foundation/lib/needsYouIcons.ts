// Icon registry for Today's "needs you" rows: a slice says which glyph and tile colour its kind of
// row uses, as it registers its screens (architecture 8.4). Kinds nobody registered keep the
// default: a clock for a row with a deadline, an open book for the rest.
export type NeedsYouTone = "mint" | "amber" | "grey";

/** Line icons on a 24px grid (board S03). */
export const GLYPHS = {
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2",
  book: "M4 5h6a2 2 0 0 1 2 2v12a2 2 0 0 0-2-2H4zM20 5h-6a2 2 0 0 0-2 2v12a2 2 0 0 1 2-2h6z",
  checklist: "M9 11l3 3 8-8M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9",
  pen: "M4 20l4-1L19 8l-3-3L5 16zM14 7l3 3",
  question: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17h.01",
  page: "M7 3h7l5 5v13H7zM14 3v5h5",
} as const;

export type Glyph = keyof typeof GLYPHS;

export interface NeedsYouIcon {
  glyph: Glyph;
  tone: NeedsYouTone;
}

const icons = new Map<string, NeedsYouIcon>();

/** Say which icon a kind of row uses. Registering a kind again replaces it. */
export function registerNeedsYouIcon(kind: string, icon: NeedsYouIcon): void {
  icons.set(kind, icon);
}

/** The icon for a row: what its slice registered, else the clock (deadline) or the book. */
export function needsYouIcon(kind: string, hasDeadline: boolean): NeedsYouIcon {
  return icons.get(kind) ?? (hasDeadline ? { glyph: "clock", tone: "amber" } : { glyph: "book", tone: "mint" });
}
