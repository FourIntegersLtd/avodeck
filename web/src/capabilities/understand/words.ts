// Slice D's interface words (en-GB), registered under `understand.`. Words that change by pack (the title of the
// one-consultation view, lane names, the clinician's role) arrive from the API, never from here (rule 11).
import { registerMessages, t } from "../../foundation/i18n";

const WORDS = {
  "today.things_to_look_at_one": "{{case}}: {{count}} thing to look at",
  "today.things_to_look_at_other": "{{case}}: {{count}} things to look at",
  "today.things_to_look_at_action": "Look at them",

  "common.back_to_timeline": "The timeline",
  "common.page": "p.{{page}}",
  "common.pages_list": "{{list}}",
  "common.no_date": "No date",
  "common.save": "Save",
  "common.cancel": "Cancel",
  "common.saved_error": "That did not save. Your work is safe. Please try again.",
  "common.stale": "Someone changed this while you were looking. It has been refreshed.",

  "timeline.title": "The timeline",
  "timeline.intro": "Everything in the record, on one line. Open any entry to see how it looked on the day.",
  "timeline.filters": "Show only",
  "timeline.filter.status": "Show",
  "timeline.filter.status.all": "Everything",
  "timeline.filter.question": "Linked to",
  "timeline.filter.question.all": "Any question",
  "timeline.filter.question.option": "Question {{n}}",
  "timeline.filter.from": "From",
  "timeline.filter.to": "To",
  "timeline.empty_lane": "Nothing in this line.",
  "timeline.unplaced": "Not on a line yet",
  "timeline.unplaced_hint": "These have no date or no line of their own, so they are listed here.",
  "timeline.linked_one": "Linked to {{count}} question",
  "timeline.linked_other": "Linked to {{count}} questions",
  "timeline.not_linked": "Not linked to a question",
  "timeline.nothing": "Nothing matches. Try clearing a filter.",

  "consultation.title": "One consultation",
  "consultation.on": "{{title}} on {{date}}",
  "consultation.hint": "Anything that arrived later is kept out of sight.",
  "consultation.seen": "In the record that day",
  "consultation.seen_none": "Nothing had arrived in the record by this day.",
  "consultation.later": "Show what came later ({{count}})",
  "consultation.later_none": "Nothing came later.",
  "consultation.unknown": "Not known when this arrived",
  "consultation.unknown_hint": "We have not placed it before this visit.",
  "consultation.arrived": "Arrived {{date}}",
  "consultation.arrival_unknown": "Arrival not known",
  "consultation.guidance": "Guidance in force that day",
  "consultation.guidance_none": "No guidance is recorded for this day.",
  "consultation.note": "Your note",
  "consultation.note_title": "Your notes on this visit",
  "consultation.note_hint": "Only you and your team see this. It never goes into your report.",
  "consultation.note_save": "Save note",
  "consultation.note_saved": "Your note is saved.",
  "consultation.change_arrival": "Change when this arrived",
  "consultation.arrival_title": "When did this arrive?",
  "consultation.arrival_date": "Date it arrived",
  "consultation.arrival_unknown_box": "It is not known when it arrived",
  "consultation.arrival_reason": "Why are you changing it?",
  "consultation.arrival_hint": "We keep every change and the reason for it.",

  "comments.title": "Comments",
  "comments.warning": "Comments are working notes. They never go into your report, but they may have to be shared if the court asks.",
  "comments.add": "Add a comment",
  "comments.add_button": "Add comment",
  "comments.none": "No comments yet.",
  "comments.resolve": "Mark as dealt with",
  "comments.resolved": "Dealt with",

  "things.title": "Things to look at",
  "things.intro": "Places where the record doesn't show what usually follows. These are prompts, not findings. You decide.",
  "things.progress": "{{done}} of {{total}} decided",
  "things.none": "Nothing to look at just now.",
  "things.pages": "Pages",
  "things.decision": "Your decision",
  "things.decision.relevant": "Relevant",
  "things.decision.not_relevant": "Not relevant",
  "things.decision.explained_elsewhere": "Explained elsewhere",
  "things.reason": "Your reason",
  "things.reason_hint": "A few words are enough.",
  "things.save": "Save my decision",
  "things.decided": "Saved.",
  "things.rerun": "Check again",
  "things.rerun_started": "We're checking again. New things may appear in a moment. Your decisions are kept.",
  "things.checks": "Your checks",
  "things.checks_hint": "Changes apply the next time we check.",
  "rule.gentle_default": "You may want to look at this entry again",
  "rule.no_action_within": "Nothing in the record shows what followed this entry",
  "rule.no_action_within.gentle": "You may want to check what followed this entry",
  "rule.not_acknowledged_within": "Nothing in the record shows this was acknowledged",
  "rule.monitoring_overdue": "Monitoring looks overdue",
  "rule.not_reached_clinician": "This may not have reached the treating team",
  "rule.actions_not_done": "Actions asked for here are not shown as done",
  "rule.field_missing": "Something expected is missing from this entry",
  "rule.not_followed_up": "Nothing in the record shows this was followed up",
};

registerMessages(Object.fromEntries(Object.entries(WORDS).map(([key, value]) => [`understand.${key}`, value])));

type Raw = keyof typeof WORDS;
/** A key, with `_one`/`_other` forms named by their base (t picks the form from `count`). */
export type WordKey = Raw extends infer K ? (K extends `${infer B}_other` ? B : K extends `${infer B}_one` ? B : K) : never;

type Params = Record<string, string | number | null | undefined>;

export function w(key: WordKey, params?: Params): string {
  return t(`understand.${key}`, params);
}
