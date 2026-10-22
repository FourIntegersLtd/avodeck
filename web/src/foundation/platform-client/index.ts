// The only way the web app talks to anything: FastAPI, through the client generated from OpenAPI
// (rule 4). The session lives in an httpOnly cookie set by the API; this file never stores tokens.
import createClient, { type Middleware } from "openapi-fetch";
import { apiOriginSetting } from "../lib/env";
import type { components, paths } from "./schema";

export type { components, paths };
export type Schemas = components["schemas"];

/** The API origin: the build's NEXT_PUBLIC_API_ORIGIN, else the page's own origin (dev proxy, same-site hosting). */
export function apiOrigin(): string {
  return (apiOriginSetting || window.location.origin).replace(/\/+$/, "");
}

/** The API's error envelope as an exception: `code` is stable and drives the words shown. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly extra: Record<string, unknown> = {},
  ) {
    super(code);
    this.name = "ApiError";
  }
}

type SignedOutListener = (code: string) => void;
const signedOutListeners = new Set<SignedOutListener>();
const SIGNED_OUT_CODES = new Set(["not_signed_in", "session_expired"]);

/** Called when the API says the session has ended. Returns a function that stops listening. */
export function onSignedOut(listener: SignedOutListener): () => void {
  signedOutListeners.add(listener);
  return () => signedOutListeners.delete(listener);
}

function readCookie(name: string): string | null {
  for (const part of document.cookie.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return null;
}

const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);

const csrf: Middleware = {
  onRequest({ request }) {
    if (SAFE.has(request.method)) return request;
    const token = readCookie("av_csrf");
    if (token) request.headers.set("X-CSRF-Token", token);
    return request;
  },
};

export const api = createClient<paths>({
  baseUrl: apiOrigin(),
  credentials: "include",
  // Looked up on each call, so test doubles of fetch are honoured.
  fetch: (request) => globalThis.fetch(request),
});
api.use(csrf);

function toApiError(status: number, body: unknown): ApiError {
  if (body && typeof body === "object" && "error" in body) {
    const { code, ...extra } = (body as { error: Record<string, unknown> }).error;
    delete extra.message; // the API's own text is for logs; screens show their own words
    if (typeof code === "string") return new ApiError(status, code, extra);
  }
  return new ApiError(status, "server_error");
}

/** Waits for a client call and returns its body, or throws ApiError. */
export async function unwrap<D>(call: Promise<{ data?: D; error?: unknown; response: Response }>): Promise<D> {
  let result: { data?: D; error?: unknown; response: Response };
  try {
    result = await call;
  } catch {
    throw new ApiError(0, "network_error");
  }
  if (result.response.ok) return result.data as D;
  const err = toApiError(result.response.status, result.error);
  if (err.status === 401 && SIGNED_OUT_CODES.has(err.code)) signedOutListeners.forEach((listener) => listener(err.code));
  throw err;
}

/** Header for actions that must not happen twice (architecture 5.1). */
export function idempotencyKey(): { "Idempotency-Key": string } {
  return { "Idempotency-Key": crypto.randomUUID() };
}

/** Header that makes an update fail with 412 if someone else changed the row first. */
export function ifMatch(etag: string): { "If-Match": string } {
  return { "If-Match": etag };
}

/** A WebSocket address on the API (dictation). */
export function websocketUrl(path: string): string {
  return apiOrigin().replace(/^http/, "ws") + path;
}
