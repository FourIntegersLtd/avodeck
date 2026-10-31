import { t } from "../i18n";
import { ApiError } from "../platform-client";
import { screenPath } from "./screens";

const SHARED: Record<string, string> = {
  network_error: "errors.network",
  too_many_attempts: "errors.too_many_attempts",
  invalid_code: "errors.invalid_code",
  invalid_token: "errors.invalid_token",
  already_confirmed: "errors.already_confirmed",
  questions_locked: "errors.questions_locked",
  served_use_supplementary: "errors.served_use_supplementary",
  mfa_required: "errors.mfa_required",
  file_too_large: "errors.file_too_large",
  upload_closed: "errors.upload_closed",
  upload_expired: "errors.upload_expired",
  upload_failed: "errors.upload_failed",
  upload_busy: "errors.upload_failed",
  hash_failed: "errors.upload_failed",
  chunk_out_of_range: "errors.upload_part_failed",
  chunk_size_wrong: "errors.upload_part_failed",
  chunk_corrupted: "errors.upload_part_failed",
  chunks_missing: "errors.chunks_missing",
  sha256_mismatch: "errors.sha256_mismatch",
  file_hash_mismatch: "errors.file_hash_mismatch",
  chunk_sha_missing: "errors.upload_part_failed",
  file_sha_missing: "errors.upload_part_failed",
  not_checked: "errors.not_checked",
  stale_link: "errors.stale_link",
};

/** Plain words for an error: a screen's own key for a code, else the shared ones. Never the code itself. */
export function errorMessage(error: unknown, own: Record<string, string> = {}): string {
  const code = error instanceof ApiError ? error.code : "server_error";
  if (own[code]) return t(own[code]);
  return t(SHARED[code] ?? "errors.generic");
}

/** A next step a screen shows beside the error words, as a link button. */
export interface ErrorAction {
  label: string;
  to: string;
}

// Codes whose next step is another screen, by its registered key (lib/screens).
const NEXT_SCREEN: Record<string, { screen: string; label: string }> = {
  served_use_supplementary: { screen: "S21b", label: "errors.served_use_supplementary_action" },
};

/** The next step for an error, if it has one and that screen is registered; else null. */
export function errorAction(error: unknown, caseId?: string | null): ErrorAction | null {
  const next = error instanceof ApiError ? NEXT_SCREEN[error.code] : undefined;
  if (!next) return null;
  const to = screenPath(next.screen, { caseId });
  return to ? { label: t(next.label), to } : null;
}
