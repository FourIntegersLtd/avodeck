// Slice A's interface words (en-GB), registered under `pipeline.`. Words that change by pack (the
// instructing party, source types, the request's label) come from the case's pack instead.
import { registerMessages, t } from "../../foundation/i18n";

const WORDS = {
  back: "Your bundle",
  page_alt: "Page {{page}}",
  page_ref: "p.{{page}}",
  "pages.range": "{{from}} to {{to}}",
  "pages.list_one": "page {{list}}",
  "pages.list_other": "pages {{list}}",

  "preparing.title": "Preparing your bundle",
  "preparing.size_one": "{{pages}} pages from {{n}} file.",
  "preparing.size_other": "{{pages}} pages from {{n}} files.",
  "preparing.left_one": "About {{n}} minute left.",
  "preparing.left_other": "About {{n}} minutes left.",
  "preparing.steps": "What we're doing",
  "preparing.step.split": "Splitting into pages",
  "preparing.step.ocr": "Reading every page",
  "preparing.step.sort": "Sorting by where each page came from",
  "preparing.step.duplicates": "Setting aside copies of the same page",
  "preparing.step.missing": "Checking what looks missing",
  "preparing.state.done": "Done",
  "preparing.state.current": "Doing this now",
  "preparing.state.waiting": "Not started yet",
  "preparing.progress": "{{pct}}% of your bundle done",
  "preparing.kept": "Your original files are kept exactly as you sent them.",
  "preparing.close": "You can close this window. We'll email you when it's ready.",
  "preparing.exhibits_one": "1 recording or image was kept as it is. Avodeck can't read these, so it's listed for you.",
  "preparing.exhibits_other": "{{n}} recordings or images were kept as they are. Avodeck can't read these, so they're listed for you.",
  "preparing.stop": "Stop preparing",
  "preparing.stopped": "Preparing has stopped. Your files are kept.",
  "preparing.start": "Start preparing",
  "preparing.nothing": "Nothing is being prepared yet. When the records are in, start preparing them here.",
  "preparing.failed": "Something went wrong while preparing your bundle. Your files are kept.",
  "preparing.again": "Try again",
  "preparing.meanwhile": "Meanwhile, check what you've been asked",

  "bundle.title": "Your bundle is ready",
  "bundle.sources": "Where the pages came from",
  "bundle.source_unknown": "Not sorted yet",
  "bundle.source_pages_one": "{{n}} page",
  "bundle.source_pages_other": "{{n}} pages",
  "bundle.numbered": "{{pages}} pages, numbered 1 to {{last}}.",
  "bundle.numbered_note": "Sorted by source and date. Each page keeps its number for good. Anything sent later is numbered after page {{last}}.",
  "bundle.copies_one": "1 copy set aside. Nothing deleted.",
  "bundle.copies_other": "{{n}} copies set aside. Nothing deleted.",
  "bundle.copies_note": "The same letter in two sets of records, for example.",
  "bundle.see_copies": "See them",
  "bundle.other_person_one": "1 page may belong to someone else.",
  "bundle.other_person_other": "{{n}} pages may belong to someone else.",
  "bundle.other_person_note": "The name or date of birth is different on {{pages}}. Nothing is hidden until you check.",
  "bundle.check": "Check them",
  "bundle.set_aside_one": "1 page set aside as not this patient's record.",
  "bundle.set_aside_other": "{{n}} pages set aside as not this patient's record.",
  "bundle.exhibits_one": "1 recording or image Avodeck can't read.",
  "bundle.exhibits_other": "{{n}} recordings or images Avodeck can't read.",
  "bundle.exhibits_note": "Kept and listed for you, but not read.",
  "bundle.foreign_one": "1 page in another language.",
  "bundle.foreign_other": "{{n}} pages in another language.",
  "bundle.foreign_page": "p.{{page}}, {{language}}",
  "bundle.foreign_note": "These may need a translation: {{pages}}.",
  "bundle.unreadable_one": "1 page we couldn't read.",
  "bundle.unreadable_other": "{{n}} pages we couldn't read.",
  "bundle.unreadable_note": "Mostly handwriting and faint scans. We'll show you these one by one.",
  "bundle.missing_one": "1 thing looks missing",
  "bundle.missing_other": "{{n}} things look missing",
  "bundle.gaps_one": "There is also 1 stretch of time with no records.",
  "bundle.gaps_other": "There are also {{n}} stretches of time with no records.",
  "bundle.files": "Your files",
  "bundle.file": "File",
  "bundle.pages": "Pages",
  "bundle.how": "How it was obtained",
  "bundle.received": "Received",
  "bundle.not_given": "Not given",
  "bundle.deliveries": "Deliveries",
  "bundle.delivery": "Delivery {{n}}: pages {{first}} to {{last}}",
  "bundle.start": "Start reading",
  "bundle.empty": "There are no pages in this bundle yet.",
  "bundle.to_preparing": "See how preparing is going",

  "copies.title": "Copies set aside",
  "copies.intro": "We found these pages twice. The copy is set aside and nothing is deleted. Keep a page if it isn't really a copy.",
  "copies.pair": "p.{{page}} is a copy of p.{{original}}",
  "copies.confirm": "Yes, it's a copy",
  "copies.keep": "Keep this page",
  "copies.status.suggested": "Please check",
  "copies.status.confirmed": "Set aside",
  "copies.status.kept": "Kept in the bundle",
  "copies.none": "No copies were found.",
  "copies.changed": "Someone else changed this page. Here is the latest. Please choose again if you need to.",

  "ask.title": "{{label}} for what's missing",
  "ask.intro": "We've written it for you. Change anything you like, then send it from your own email.",
  "ask.to": "To",
  "ask.to_hint": "Their email address. You can leave it empty and add it in your email.",
  "ask.subject": "Subject",
  "ask.body": "Message",
  "ask.private": "Patient details are left out. Page numbers are from your bundle.",
  "ask.edit": "Edit the email",
  "ask.done_editing": "Done editing",
  "ask.next": "What happens next",
  "ask.next_noted": "We note the request in your case record, with the date.",
  "ask.next_arrive": "When the records arrive, add them to the case. They're numbered after page {{last}}, so nothing you've already read moves.",
  "ask.next_keep": "You can keep reading meanwhile.",
  "ask.copy": "Copy the text",
  "ask.copied": "Copied.",
  "ask.send": "Send from my email",
  "ask.sent": "Your email is open, ready to send. We've noted the request in your case record.",
  "ask.check": "Please check the email address and the message.",

  "quarantine.title": "These pages may belong to someone else",
  "quarantine.intro": "The name or date of birth on them doesn't match {{name}}. Check each one. Nothing is hidden until you say so.",
  "quarantine.reason.identifiers_differ": "Different name or date of birth",
  "quarantine.not": "Not {{name}}'s",
  "quarantine.is": "It is {{name}}'s",
  "quarantine.set_aside": "Set aside",
  "quarantine.undo": "Undo",
  "quarantine.undone": "Back in the bundle.",
  "quarantine.purged": "This page has been deleted, so it can't be brought back.",
  "quarantine.explain": "What happens when you set them aside",
  "quarantine.e_numbers": "Page numbers stay the same.",
  "quarantine.e_numbers_note": "Each page is replaced by a note saying it was removed, when and why.",
  "quarantine.e_unused": "Nothing from them is used.",
  "quarantine.e_unused_note": "Not in search, suggestions, your report or anything you download.",
  "quarantine.e_told": "The {{party}} is told.",
  "quarantine.e_told_anyone": "The people who instructed you are told.",
  "quarantine.e_told_note": "We draft a short note for you to send.",
  "quarantine.e_deleted": "Then they're deleted.",
  "quarantine.e_deleted_note": "After the note is sent, or after a set time. You can undo until then.",
  "quarantine.later": "Decide later",
  "quarantine.submit_one": "Set aside {{n}} page and draft the note",
  "quarantine.submit_other": "Set aside {{n}} pages and draft the note",
  "quarantine.save": "Save my choices",
  "quarantine.only_expert": "Only the named expert can decide about these pages.",
  "quarantine.none": "No pages are waiting to be checked.",
} as const;

registerMessages(Object.fromEntries(Object.entries(WORDS).map(([k, v]) => [`pipeline.${k}`, v])));

type Raw = keyof typeof WORDS;
/** A key, with `_one`/`_other` forms named by their base (t picks the form from `count`). */
export type WordKey = Raw extends infer K ? (K extends `${infer B}_other` ? B : K extends `${infer B}_one` ? B : K) : never;

type Params = Record<string, string | number | null | undefined>;

export function w(key: WordKey, params?: Params): string {
  return t(`pipeline.${key}`, params);
}

/** A counted phrase: `count` picks the _one/_other form, `{{n}}` shows it in the locale's digits. */
export function wn(key: WordKey, count: number, locale: string | null | undefined, params: Params = {}): string {
  let n = String(count);
  try {
    n = new Intl.NumberFormat(locale ?? undefined).format(count);
  } catch {
    // an unknown locale shows plain digits
  }
  return t(`pipeline.${key}`, { ...params, count, n });
}

/** A word for a pack- or API-given code under `prefix`, else the code in plain form. */
export function wordFor(prefix: string, code: string, fallback: string): string {
  return t(`pipeline.${prefix}.${code}`, {}, fallback);
}
