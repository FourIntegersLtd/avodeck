// The only way the web app talks to anything: FastAPI, through the client generated from OpenAPI
// (rule 4). The session lives in an httpOnly cookie set by the API; this file never stores tokens.
import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./schema";

export type { components, paths };
export type Schemas = components["schemas"];

/** The API origin: the build's VITE_API_ORIGIN, else the page's own origin (dev proxy, same-site hosting). */
export function apiOrigin(): string {
  return (import.meta.env.VITE_API_ORIGIN || window.location.origin).replace(/\/+$/, "");
}

/** The API's error envelope as an exception: `code` is stable and drives the words shown. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly extra: Record<string, unknown> = {},
    /** True when the code prompt was already shown for this call (cancelled, or still asked for). */
    readonly stepUpAsked = false,
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

type MutationListener = () => void;
const mutationListeners = new Set<MutationListener>();

/** Called when a change (POST, PUT, PATCH, DELETE) is sent and again when it is answered, so kept
 * answers (foundation/lib/resourceCache.ts) are dropped. Returns a function that stops listening. */
export function onMutation(listener: MutationListener): () => void {
  mutationListeners.add(listener);
  return () => mutationListeners.delete(listener);
}

const mutations: Middleware = {
  onRequest({ request }) {
    if (!SAFE.has(request.method)) mutationListeners.forEach((listener) => listener());
    return request;
  },
  onResponse({ request, response }) {
    if (!SAFE.has(request.method)) mutationListeners.forEach((listener) => listener());
    return response;
  },
};

const csrf: Middleware = {
  onRequest({ request }) {
    if (SAFE.has(request.method)) return request;
    const token = readCookie("av_csrf");
    if (token) request.headers.set("X-CSRF-Token", token);
    return request;
  },
};

/** Rule 10: who asks for the code when a call answers step_up_required (foundation/lib/stepUp.tsx). */
export interface StepUpPrompt {
  /** Shows the code prompt; true once the code is accepted, false if cancelled. */
  ask(): Promise<boolean>;
  /** The code was given but the call still needs it: tell the person nothing was changed. */
  nothingChanged(): void;
}

let stepUpPrompt: StepUpPrompt | null = null;
let asking: Promise<boolean> | null = null;
const ASKED = "x-avodeck-step-up-asked";

/** Registers the code prompt. Returns a function that removes it. */
export function onStepUpRequired(prompt: StepUpPrompt): () => void {
  stepUpPrompt = prompt;
  return () => {
    if (stepUpPrompt === prompt) stepUpPrompt = null;
  };
}

async function needsCode(response: Response): Promise<boolean> {
  if (response.status !== 403) return false;
  try {
    const body = (await response.clone().json()) as { error?: { code?: unknown } };
    return body.error?.code === "step_up_required";
  } catch {
    return false;
  }
}

function markAsked(response: Response): Response {
  const headers = new Headers(response.headers);
  headers.set(ASKED, "1");
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

/** Sends a request; if it needs the code, asks once (shared by calls in flight) and sends it again once. */
async function send(request: Request): Promise<Response> {
  const prompt = stepUpPrompt;
  const spare = prompt && !new URL(request.url).pathname.startsWith("/v1/auth/") ? request.clone() : null;
  const first = await globalThis.fetch(request); // looked up on each call, so test doubles of fetch are honoured
  if (!prompt || !spare || !(await needsCode(first))) return first;
  asking ??= prompt.ask().finally(() => {
    asking = null;
  });
  if (!(await asking)) return markAsked(first);
  const again = await globalThis.fetch(spare);
  if (!(await needsCode(again))) return again;
  prompt.nothingChanged();
  return markAsked(again);
}

export const api = createClient<paths>({
  baseUrl: apiOrigin(),
  credentials: "include",
  fetch: send,
});
api.use(csrf, mutations);

function toApiError(status: number, body: unknown, stepUpAsked = false): ApiError {
  if (body && typeof body === "object" && "error" in body) {
    const { code, ...extra } = (body as { error: Record<string, unknown> }).error;
    delete extra.message; // the API's own text is for logs; screens show their own words
    if (typeof code === "string") return new ApiError(status, code, extra, stepUpAsked);
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
  const err = toApiError(result.response.status, result.error, result.response.headers.get(ASKED) === "1");
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
