// Screens 14 and 15's words (docs/spec/glossary.md). Registered with registerMessages under "write.";
// `w()` only accepts keys defined here. Side labels, view labels and the legal test arrive from the
// pack through the API, never from this file.
import { registerMessages, t } from "../../foundation/i18n";

const WORDS = {
  "tab.question": "Question {{number}}",
  "nothing_to_answer": "There are no questions to answer yet. Check the questions you've been asked first.",
  "open_questions": "Check the questions",

  "points.title": "Points against",
  "points.intro": "Before you give your view, set out the facts on each side. The court expects you to have weighed the points that cut against you.",
  "points.side_empty": "Nothing here yet.",
  "points.page": "p.{{page}}",
  "points.flagged": "You flagged this",
  "points.keep": "Keep",
  "points.move": "Move to {{side}}",
  "points.remove": "Remove",
  "points.remove_title": "Why are you removing this?",
  "points.remove_reason": "A few words",
  "points.remove_confirm": "Remove it",
  "points.cancel": "Cancel",
  "points.unused": "Facts not on a side yet",
  "points.unused_empty": "Every fact for this question is on a side.",
  "points.add_to": "Add to {{side}}",
  "points.relevant_title_one": "You haven't used {{count}} relevant fact yet.",
  "points.relevant_title_other": "You haven't used {{count}} relevant facts yet.",
  "points.relevant_dismiss": "Not needed",
  "points.differ": "Where the accounts differ",
  "points.differ_none": "No accounts differ from the record.",
  "points.statement": "The statement:",
  "points.record": "The record:",
  "points.record_none": "Nothing on the record matches.",
  "points.claim_confirm": "This is right",
  "points.claim_dismiss": "Ignore this",
  "points.judge": "It's for the judge to decide which is right. Your report will give your view on each version.",
  "points.range_save": "Save my note",
  "points.range_saved": "Saved.",
  "points.write": "Write my opinion",
  "points.stale": "This changed in another window. We've shown you the latest.",

  "opinion.title": "Your opinion",
  "opinion.facts": "The facts for question {{number}}",
  "opinion.facts_hint": "Click a fact to put it in your text with its page.",
  "opinion.facts_empty": "No facts are ready to cite yet. Facts you have checked will show here.",
  "opinion.guidance": "Guidance on {{date}}",
  "opinion.guidance_force": "In force on the day",
  "opinion.points": "Your points against",
  "opinion.points_summary": "{{count}} facts for {{side}}",
  "opinion.points_differ_one": "{{count}} account differs",
  "opinion.points_differ_other": "{{count}} accounts differ",
  "opinion.points_open": "Open",
  "opinion.editor": "Your opinion on this question",
  "opinion.yours_only": "Your words only. We never write your opinion.",
  "opinion.view": "Your view",
  "opinion.provisional": "My view is provisional",
  "opinion.test": "The test you're applying",
  "opinion.test_reference": "The test the case type uses, for reference:",
  "opinion.checks": "Checks on what you've written",
  "opinion.checks_clear": "Nothing to check.",
  "opinion.issue.uncited_fact": "A sentence states a fact without a page number: {{words}}",
  "opinion.issue.loaded_language": "Loaded words: {{words}}",
  "opinion.issue.outside_expertise": "This may be outside your expertise: {{words}}",
  "opinion.issue.other": "Please look at: {{words}}",
  "opinion.issue_dismiss": "Dismiss",
  "opinion.dismiss_title": "Why are you dismissing this check?",
  "opinion.dismiss_reason": "A few words",
  "opinion.dismiss_confirm": "Dismiss it",
  "opinion.saving": "Saving…",
  "opinion.saved": "Saved",
  "opinion.unsaved": "Not saved yet",
  "opinion.save_failed": "We couldn't save just now. We'll try again.",
  "opinion.cite_failed": "One of your facts can't be cited yet. Check that it is verified.",
  "opinion.versions": "Earlier versions",
  "opinion.versions_empty": "There are no earlier versions yet.",
  "opinion.version_item": "Version {{number}}, saved {{when}}",
  "opinion.restore": "Go back to version {{number}}",
  "opinion.restored": "Restored.",
  "opinion.stale_title": "You changed this in another window. Which version do you want to keep?",
  "opinion.stale_yours": "Keep what I wrote here",
  "opinion.stale_saved": "Keep the saved version",
  "opinion.next": "Next question",
  "opinion.back_to_points": "Back to points against",

  "today.questions_without_opinion_one": "{{case}}: {{count}} question without your opinion",
  "today.questions_without_opinion_other": "{{case}}: {{count}} questions without your opinion",
  "today.questions_without_opinion_action": "Write your opinion",
  "today.points_not_considered_one": "{{case}}: {{count}} point against not yet considered",
  "today.points_not_considered_other": "{{case}}: {{count}} points against not yet considered",
  "today.points_not_considered_action": "Look at the points",
} as const;

export type WriteKey = keyof typeof WORDS;

export const WRITE_MESSAGES: Record<string, string> = Object.fromEntries(
  Object.entries(WORDS).map(([key, value]) => [`write.${key}`, value]),
);

export function registerWriteMessages(): void {
  registerMessages(WRITE_MESSAGES);
}

export function w(key: WriteKey, params: Record<string, string | number | null | undefined> = {}): string {
  return t(`write.${key}`, params);
}

registerWriteMessages();
