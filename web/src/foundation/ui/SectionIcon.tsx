// One quiet line glyph that gives a card its identity (the IconTile pattern on S03 Today and S03e Cases).
// Every slice draws from this one vocabulary so the marks look like a single family: one per card header,
// never per row, never decorative. When no glyph fits, leave the header plain — restraint over coverage.
import { IconTile } from "./Callout";

/** Line icons on a 24px grid, stroke 2.25, round joins. Each entry is one or more path `d` strings. */
export const SECTION_GLYPHS = {
  page: ["M7 3h8l4 4v14H7z", "M15 3v4h4", "M10 13h6M10 16h4"],
  folder: ["M4 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"],
  people: ["M15.5 11a3 3 0 1 0-6 0 3 3 0 0 0 6 0z", "M4 20c0-3.2 3-5.5 8.5-5.5S21 16.8 21 20"],
  person: ["M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M4 21c0-4 4-6.5 8-6.5s8 2.5 8 6.5"],
  calendar: ["M5 6h14v14H5z", "M5 10h14", "M9 3v4M15 3v4"],
  clock: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M12 7v5l3 2"],
  mic: ["M9 5a3 3 0 0 1 6 0v6a3 3 0 0 1-6 0z", "M5 11a7 7 0 0 0 14 0", "M12 18v3"],
  pen: ["M4 20l4-1L19 8l-3-3L5 16z", "M14 7l3 3"],
  question: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17h.01"],
  book: ["M4 5h6a2 2 0 0 1 2 2v12a2 2 0 0 0-2-2H4zM20 5h-6a2 2 0 0 0-2 2v12a2 2 0 0 1 2-2h6z"],
  checklist: ["M9 11l3 3 8-8M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9"],
  shield: ["M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"],
  shieldCheck: ["M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z", "M9 12l2 2 4-4"],
  mail: ["M4 6h16v12H4z", "M4 7l8 6 8-6"],
  gavel: ["M14 4l6 6M11 7l6 6M4 20l7-7M9 5l10 10"],
  invoice: ["M6 3h12v18l-3-2-3 2-3-2-3 2z", "M9 8h6M9 12h6"],
  signature: ["M4 18c3 0 4-8 6-8s2 6 4 6 3-2 6-2", "M4 21h16"],
  search: ["M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z", "M20 20l-4-4"],
  lock: ["M6 11h12v9H6z", "M9 11V8a3 3 0 0 1 6 0v3"],
  flag: ["M6 21V4h11l-2 4 2 4H6"],
  timeline: ["M4 7h16M4 12h16M4 17h10", "M8 5v4M14 10v4"],
  send: ["M4 12l16-7-7 16-2-7z"],
  download: ["M12 4v10M8 11l4 4 4-4", "M5 20h14"],
  home: ["M4 11l8-7 8 7", "M6 10v10h12V10"],
} as const;

export type SectionGlyph = keyof typeof SECTION_GLYPHS;

/** The icon-tile that leads a card's heading. `tone` is mint by default; amber or grey for a change of weather. */
export function SectionIcon({ glyph, tone = "mint", size = 26 }: { glyph: SectionGlyph; tone?: "mint" | "amber" | "grey"; size?: number }) {
  return (
    <IconTile tone={tone}>
      <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round">
        {SECTION_GLYPHS[glyph].map((d) => (
          <path key={d} d={d} />
        ))}
      </svg>
    </IconTile>
  );
}
