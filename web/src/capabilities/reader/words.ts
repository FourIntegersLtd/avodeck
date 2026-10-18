// Slice B's interface words (en-GB), registered under `reader.`. Words that change by pack (source
// types) come from the case's pack instead.
import { registerMessages, t } from "../../foundation/i18n";

const WORDS = {
  "read.loading": "Opening the page",
  "read.page_alt": "Page {{page}}",
  "read.page_ref": "p.{{page}}",
  "read.in_period": "You're in the period in question.",
  "read.in_period_after": "You read this page first; our suggestions are now shown.",
  "read.zoom_in": "Zoom in",
  "read.zoom_out": "Zoom out",
  "read.zoom_level": "{{percent}}%",
  "read.found": "What we found on this page",
  "read.not_read_yet": "Mark this page as read to see our suggestions.",
  "read.none_found": "We found nothing to check on this page.",
  "read.mark_read": "Mark as read",
  "read.next": "Next page",
  "read.previous": "Previous page",
  "read.progress_one": "Pages you've read: {{read}} of {{total}}, about {{n}} minute left",
  "read.progress_other": "Pages you've read: {{read}} of {{total}}, about {{n}} minutes left",
  "read.progress_done": "Pages you've read: {{read}} of {{total}}. You've read every page.",
  "read.couldnt_read_one": "{{count}} page we couldn't read",
  "read.couldnt_read_other": "{{count}} pages we couldn't read",
  "read.keys.heading": "Keyboard, if you like it",
  "read.keys.help": "R mark as read · 1 correct · 2 not right · 3 change a detail · J next page · K previous page",
  "read.error.stale": "This suggestion changed since you opened it. We've shown the latest.",
  "read.error.not_in_page": "That isn't in the words on this page.",
  "read.error.generic": "That didn't work. Please try again.",

  "fact.correct": "Correct",
  "fact.not_right": "Not right",
  "fact.change": "Change a detail",
  "fact.undo": "Undo",
  "fact.why_not": "Why not? (optional)",
  "fact.right_words": "The right words",
  "fact.save_change": "Save change",
  "fact.cancel": "Cancel",
  "fact.points_against": "Send to Points against",
  "fact.points_against_off": "Take out of Points against",
  "fact.in_points_against": "In Points against",

  "unreadable.title": "Pages we couldn't read",
  "unreadable.lead": "You can read these better than we can. Tell us what's on each one, in a few words.",
  "unreadable.back": "Back to reading",
  "unreadable.done": "{{done}} of {{total}} done",
  "unreadable.queue": "Pages to type out",
  "unreadable.queue_item": "p.{{page}}",
  "unreadable.queue_done": "done",
  "unreadable.handwriting": "Handwriting",
  "unreadable.text_label": "What does this page show?",
  "unreadable.relevance": "Is this relevant to the questions you've been asked?",
  "unreadable.relevant": "Relevant",
  "unreadable.background": "Background only",
  "unreadable.clearer": "Ask for a clearer copy",
  "unreadable.clearer_asked": "Okay: a clearer copy is on your list.",
  "unreadable.save_next": "Save and next page",
  "unreadable.saved": "Saved.",
  "unreadable.all_done": "All done. Thank you.",
  "unreadable.error.unavailable": "Your typing isn't saved yet: we can't take it in just now. Please try again later.",
  "unreadable.error.generic": "That didn't work. Please try again.",

} as const;

// Today's items use `reader.today.*`, which live in the base file (en-GB.json).
registerMessages(Object.fromEntries(Object.entries(WORDS).map(([k, v]) => [`reader.${k}`, v])));

type Raw = keyof typeof WORDS;
export type WordKey = Raw extends infer K ? (K extends `${infer B}_other` ? B : K extends `${infer B}_one` ? B : K) : never;
type Params = Record<string, string | number | null | undefined>;

export function w(key: WordKey, params?: Params): string {
  return t(`reader.${key}`, params);
}
