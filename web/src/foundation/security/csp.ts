// The Content Security Policy for the Next build (rule 4: the browser talks only to FastAPI).
// Next stamps its own bootstrap scripts with a per-request nonce, so the policy is built in
// proxy.ts for every request, not baked into a static file as the Vite build did (web/csp.ts).

export interface PolicyOptions {
  /** The API's origin when it is not the page's own (empty: same origin, the supported setup). */
  apiOrigin: string;
  /** Development needs eval for hot reload; a production build never gets it. */
  isDev: boolean;
}

/** A fresh nonce from the platform's random number generator. */
export function newNonce(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return btoa(String.fromCharCode(...bytes));
}

/** The policy string for one request carrying `nonce`. */
export function buildPolicy(nonce: string, { apiOrigin, isDev }: PolicyOptions): string {
  const api = apiOrigin.replace(/\/+$/, "");
  const connect = ["'self'"];
  if (api) connect.push(api, api.replace(/^http/, "ws"));
  const script = ["'self'", `'nonce-${nonce}'`, "'strict-dynamic'"];
  if (isDev) script.push("'unsafe-eval'");
  const directives: Array<[string, string[]]> = [
    ["default-src", ["'self'"]],
    ["connect-src", connect],
    ["script-src", script],
    ["style-src", ["'self'", `'nonce-${nonce}'`]],
    ["img-src", ["'self'", "data:", "blob:"]], // blob: is an object URL made from bytes FastAPI streamed
    ["font-src", ["'self'"]],
    ["media-src", ["'self'", "blob:"]],
    ["object-src", ["'none'"]],
    ["base-uri", ["'none'"]],
    ["form-action", ["'self'"]],
    ["frame-ancestors", ["'none'"]],
  ];
  return directives.map(([name, sources]) => `${name} ${sources.join(" ")}`).join("; ") + ";";
}
