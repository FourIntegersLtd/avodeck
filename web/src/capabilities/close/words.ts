// Screen 26's words (docs/spec/glossary.md). Registered with registerMessages under "close.";
// `w()` only accepts keys defined here, so a missing key fails the type check.
// Pack words (the instructing party, retention years) arrive as parameters, never written here.
import { registerMessages, t } from "../../foundation/i18n";

const WORDS = {
  title: "Close the case",
  "heading.active": "Close this case",
  "heading.served": "Close this case",
  "heading.settled": "The case has settled",
  "heading.archived": "This case is in your archive",
  "heading.withdrawn": "This case was withdrawn",
  "heading.deleted": "This case has been deleted",
  "intro.open": "Three things to decide, then you're done.",
  "intro.settled": "Settled on {{date}}. Three things to decide, then you're done.",
  "intro.deleting": "We are deleting this case now. Nothing else can change.",

  "copies.heading": "Keep your copies",
  "copies.lead": "Download your own record of the work. These are yours to keep.",
  "copies.reports": "Every version of your report",
  "copies.method": "How you worked: the method log",
  "copies.reads": "Pages you read, with dates",
  "copies.invoice": "Your invoice and hours",
  "copies.admin_lead": "As an administrator you get the reports, file checks and logs. The medical records are not included.",
  "copies.make": "Get my files ready",
  "copies.making": "We're getting your files ready. This can take a few minutes.",
  "copies.ready": "Your files are ready. You can download them once.",
  "copies.download": "Download my files",
  "copies.downloaded": "Downloaded. Keep the file somewhere safe.",
  "copies.gone": "These files were already downloaded. Get them ready again if you need another copy.",
  "copies.failed": "We couldn't get your files ready. Please try again.",

  "keep.heading": "How long we hold the records",
  "keep.lead": "The medical records belong to the case, not to you. Pick what your instructions say.",
  "keep.legend": "How long we hold the records",
  "keep.delete_now": "Delete now",
  "keep.delete_now_hint": "Only for a case where no report was sent.",
  "keep.delete_now_served": "Not available: a report was sent on this case.",
  "keep.delete_now_held": "Not available while deletion is paused.",
  "keep.keep_default": "Keep for {{years}} years",
  "keep.usual": "Your usual setting",
  "keep.return_then_delete": "Return to the {{party}}, then delete",
  "keep.return_then_delete_plain": "Return the records to whoever sent them, then delete",
  "keep.save": "Save this choice",
  "keep.saved": "Saved.",
  "keep.confirm_title": "Delete this case now?",
  "keep.confirm_body": "Everything on this case is deleted today: the records, your notes and your report drafts. This can't be undone. We'll send you a certificate.",
  "keep.confirm_yes": "Yes, delete it now",
  "keep.confirm_no": "No, keep it",

  "proof.heading": "Proof of deletion",
  "proof.lead": "On the day, we delete the records and send you a certificate.",
  "proof.certificate": "Certificate of deletion",
  "proof.scheduled": "Scheduled for {{date}}",
  "proof.not_scheduled": "Not scheduled yet. The date is set when the case is closed.",
  "proof.paused": "Paused. Nothing is deleted while deletion is paused.",
  "proof.deleted_on": "Deleted on {{date}}",
  "proof.backups": "Last backup copy gone by {{date}}",
  "proof.removed": "Items removed: {{count}}",
  "proof.files": "Files removed: {{count}}",

  "hold.lead": "Is there an appeal or a related claim? Pause deletion until a date you choose. Nothing is deleted while it's paused.",
  "hold.pause": "Pause deletion",
  "hold.paused_until": "Deletion paused until {{date}}",
  "hold.paused": "Deletion paused until you resume it",
  "hold.resume": "Resume deletion",
  "hold.dialog_title": "Pause deletion",
  "hold.reason": "Why?",
  "hold.reason.appeal": "An appeal",
  "hold.reason.related_claim": "A related claim",
  "hold.reason.complaint": "A complaint",
  "hold.reason.other": "Something else",
  "hold.until": "Pause until (optional)",
  "hold.until_hint": "Leave empty to pause until you resume it.",
  "hold.confirm": "Pause deletion",
  "hold.cancel": "Cancel",

  "close.note": "Closed cases move to your archive. You can reopen one if you're contacted again.",
  "close.settle": "Close case",
  "close.archive": "Move to archive",
  "close.reopen": "Reopen case",
  "close.done": "Done.",

  "error.conflict": "This can't be done at this stage of the case.",
  "error.forbidden": "Only the expert on this case can do this.",
} as const;

export type CloseKey = keyof typeof WORDS;

export const CLOSE_MESSAGES: Record<string, string> = Object.fromEntries(
  Object.entries(WORDS).map(([key, value]) => [`close.${key}`, value]),
);

/** The deletion-paused message on the bell (notifications.kind.<kind>). */
export const NOTIFICATION_MESSAGES: Record<string, string> = {
  "notifications.kind.deletion_paused": "Deletion of {{case}} is paused while it is on hold.",
};

export function registerCloseMessages(): void {
  registerMessages({ ...CLOSE_MESSAGES, ...NOTIFICATION_MESSAGES });
}

export function w(key: CloseKey, params: Record<string, string | number | null | undefined> = {}): string {
  return t(`close.${key}`, params);
}

registerCloseMessages();
