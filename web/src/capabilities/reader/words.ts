// Slice B's interface words (en-GB), registered under `reader.`. Words that change by pack (source
// types) come from the case's pack instead.
import { registerMessages, t } from "../../foundation/i18n";

const WORDS = {
  "read.loading": "Opening the page",
  "read.page_alt": "Page {{page}}",
  "read.page_ref": "p.{{page}}",
  "read.in_period": "You're in the period in question.",
  "read.period_range": "({{from}} to {{to}})",
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
  "read.turn": "Turn page",
  "read.turning": "We're reading the page again. This takes a minute.",
  "read.clearer": "Clearer copy",
  "read.clearer_asked": "Okay: a clearer copy is on your list.",
  "read.not_patient": "Not this patient",
  "read.why": "Why we do this",
  "read.why_text": "We show our suggestions only after you have read the page, so your own reading comes first and nothing we suggest can steer what you notice.",
  "read.related": "{{kind}} · {{date}}",
  "read.related_checked": "{{value}} · checked by you",
  "read.related_open": "{{value}} · not checked yet",
  "read.keys.heading": "Keyboard, if you like it",
  "read.keys.correct": "correct",
  "read.keys.not_right": "not right",
  "read.keys.next": "next page",
  "read.keys.more": "Also: R mark as read · 3 change a detail · J and K change page",
  "read.error.stale": "This suggestion changed since you opened it. We've shown the latest.",
  "read.error.not_in_page": "That isn't in the words on this page.",
  "read.error.generic": "That didn't work. Please try again.",
  "read.error.words_needed": "Choose the words on the page that show this.",
  "read.error.invalid_fields": "Those details don't fit this kind of fact.",

  "fact.correct": "Correct",
  "fact.not_right": "Not right",
  "fact.change": "Change a detail",
  "fact.undo": "Undo",
  "fact.why_not": "Why not? (optional)",
  "fact.right_words": "The right words",
  "fact.save_change": "Save change",
  "fact.choose_words": "Choose the words on the page that show this.",
  "fact.cancel": "Cancel",
  "fact.points_against": "Send to Points against",
  "fact.points_against_off": "Take out of Points against",
  "fact.in_points_against": "In Points against",
  "fact.recheck": "This page was read again. Please check this is still right.",

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
  "unreadable.relevant": "Yes",
  "unreadable.background": "No, background only",
  "unreadable.clearer": "I can't read it either: ask for a clearer copy",
  "unreadable.choose": "Or choose one",
  "unreadable.nothing": "Nothing relevant on this page",
  "unreadable.is_copy": "It's a copy of another page",
  "unreadable.copy_of": "Which page is it a copy of?",
  "unreadable.copy_set": "Set this page aside",
  "unreadable.copy_error": "We couldn't set that page aside. Check the page number and try again.",
  "unreadable.add_clearer": "I have a clearer copy: add it",
  "unreadable.add_error": "We couldn't add that copy just now. Please try again.",
  "unreadable.dictation_label": "What does this page show?",
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
