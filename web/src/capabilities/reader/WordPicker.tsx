// Choosing the words a changed fact cites (rule 2). Each word on the page is a toggle drawn over the
// picture at its place (fractions of the page, so it sits right at any zoom). Words the expert typed
// out have no place on the picture; they are listed as toggles in the card instead.
import type { Schemas } from "../../foundation/platform-client";
import { Button } from "../../foundation/ui";
import { pct } from "./data";

export type PageWord = Schemas["PageWord"];

interface PickerProps {
  words: PageWord[];
  chosen: string[];
  onToggle: (wordId: string) => void;
}

/** Toggles over the page. They are as large as the words they cover (inline targets on the page itself);
 * every one is reachable by Tab and toggled with Space or Enter. */
export function WordToggles({ words, chosen, onToggle }: PickerProps) {
  return (
    <>
      {words.map((word) => {
        const b = word.box as { x0: number; y0: number; x1: number; y1: number } | null;
        if (!b) return null;
        const on = chosen.includes(word.word_id);
        return (
          <button
            key={word.word_id}
            type="button"
            className={on ? "rd-word rd-word--on" : "rd-word"}
            aria-pressed={on}
            aria-label={word.text}
            style={{ left: pct(b.x0), top: pct(b.y0), width: pct(b.x1 - b.x0), height: pct(b.y1 - b.y0) }}
            onClick={() => onToggle(word.word_id)}
          />
        );
      })}
    </>
  );
}

/** Toggles for words with no place on the picture (typed out by the expert). */
export function TypedWordToggles({ words, chosen, onToggle }: PickerProps) {
  const typed = words.filter((word) => !word.box);
  if (typed.length === 0) return null;
  return (
    <div className="rd-typed">
      {typed.map((word) => (
        <Button key={word.word_id} variant="secondary" className={chosen.includes(word.word_id) ? "rd-typed--on" : undefined} aria-pressed={chosen.includes(word.word_id)} onClick={() => onToggle(word.word_id)}>
          {word.text}
        </Button>
      ))}
    </div>
  );
}

/** The chosen ids in the page's reading order (ids the page no longer lists stay, at the end). */
export function inPageOrder(chosen: string[], words: PageWord[] | null): string[] {
  const listed = (words ?? []).map((word) => word.word_id).filter((id) => chosen.includes(id));
  return [...listed, ...chosen.filter((id) => !listed.includes(id))];
}
