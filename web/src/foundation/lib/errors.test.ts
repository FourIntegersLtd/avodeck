// Plain words for the two kinds of 409 on questions, each with its own i18n key.
import { expect, test } from "vitest";
import { t } from "../i18n";
import { ApiError } from "../platform-client";
import { errorMessage } from "./errors";

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
