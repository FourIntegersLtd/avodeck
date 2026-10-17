import { t } from "../i18n";
import { ApiError } from "../platform-client";

/** Plain words for an error: a screen's own key for a code, else the shared ones. Never the code itself. */
export function errorMessage(error: unknown, own: Record<string, string> = {}): string {
  const code = error instanceof ApiError ? error.code : "server_error";
  if (own[code]) return t(own[code]);
  const shared: Record<string, string> = {
    network_error: "errors.network",
    too_many_attempts: "errors.too_many_attempts",
    invalid_code: "errors.invalid_code",
    invalid_token: "errors.invalid_token",
  };
  return t(shared[code] ?? "errors.generic");
}
