// Plain words for the two kinds of 409 on questions, each with its own i18n key.
import { expect, test } from "vitest";
import { t } from "../i18n";
import { ApiError } from "../platform-client";
import { errorAction, errorMessage } from "./errors";
import { registerScreen, resetScreens } from "./screens";

test("already_confirmed and questions_locked each have their own words", () => {
  const confirmed = errorMessage(new ApiError(409, "already_confirmed"));
  const locked = errorMessage(new ApiError(409, "questions_locked"));
  expect(confirmed).toBe(t("errors.already_confirmed"));
  expect(locked).toBe(t("errors.questions_locked"));
  expect(confirmed).not.toBe(t("errors.generic"));
  expect(locked).not.toBe(t("errors.generic"));
  expect(confirmed).not.toBe(locked);
});

test("a screen's own words for a code still win", () => {
  expect(errorMessage(new ApiError(409, "already_confirmed"), { already_confirmed: "errors.load" })).toBe(t("errors.load"));
});

// Stuck-trap fixes: every code a person can meet on upload, report, link and sign-in has its own words.
const OWN_WORDS = [
  "served_use_supplementary",
  "mfa_required",
  "file_too_large",
  "upload_closed",
  "chunk_out_of_range",
  "chunk_size_wrong",
  "chunk_corrupted",
  "sha256_mismatch",
  "file_hash_mismatch",
  "chunk_sha_missing",
  "file_sha_missing",
  "chunks_missing",
  "size_mismatch",
  "chunk_unreadable",
  "chunk_hash_mismatch",
  "not_checked",
  "stale_link",
];

test.each(OWN_WORDS)("%s has plain words of its own, never the code or the generic line", (code) => {
  const words = errorMessage(new ApiError(409, code));
  expect(words).not.toBe(t("errors.generic"));
  expect(words).not.toContain(code);
  expect(words).not.toMatch(/errors\./);
});

test("a file that is too large tells the person to split it", () => {
  expect(errorMessage(new ApiError(422, "file_too_large"))).toMatch(/split it into smaller files/i);
});

test("a served report offers the way to correct or update it", () => {
  resetScreens();
  registerScreen("S21b", ({ caseId }) => `/cases/${caseId}/correct-report`);
  const action = errorAction(new ApiError(409, "served_use_supplementary"), "c1");
  expect(action).toEqual({ label: t("errors.served_use_supplementary_action"), to: "/cases/c1/correct-report" });
  expect(errorMessage(new ApiError(409, "served_use_supplementary"))).toMatch(/served/i);
});

test("no next step is offered for other codes, or before the screen is registered", () => {
  resetScreens();
  expect(errorAction(new ApiError(409, "served_use_supplementary"), "c1")).toBeNull();
  registerScreen("S21b", ({ caseId }) => `/cases/${caseId}/correct-report`);
  expect(errorAction(new ApiError(409, "file_too_large"), "c1")).toBeNull();
  expect(errorAction(new Error("boom"), "c1")).toBeNull();
});
