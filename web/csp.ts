// The Content Security Policy the built page ships with (rule 4: the browser talks only to FastAPI).
// It is written into index.html at build time, so it holds wherever the static files are hosted.
import type { Plugin } from "vite";

/** The policy for a build whose API lives at `apiOrigin` (empty: the API shares the page's origin). */
export function contentSecurityPolicy(apiOrigin = ""): string {
  const api = apiOrigin.replace(/\/+$/, "");
  const connect = ["'self'"];
  if (api) connect.push(api, api.replace(/^http/, "ws"));
  const directives: Array<[string, string[]]> = [
    ["default-src", ["'self'"]],
    ["connect-src", connect],
    ["script-src", ["'self'"]],
    ["style-src", ["'self'"]],
    ["img-src", ["'self'", "data:"]],
    ["font-src", ["'self'"]],
    ["media-src", ["'self'", "blob:"]],
    ["object-src", ["'none'"]],
    ["base-uri", ["'none'"]],
    ["form-action", ["'self'"]],
  ];
  return directives.map(([name, sources]) => `${name} ${sources.join(" ")}`).join("; ");
}

/** Adds the policy to index.html in production builds (the dev server needs inline scripts). */
export function cspPlugin(): Plugin {
  let apiOrigin = "";
  return {
    name: "avodeck-csp",
    apply: "build",
    configResolved(config) {
      apiOrigin = String(config.env.VITE_API_ORIGIN ?? "");
    },
    transformIndexHtml() {
      return [{ tag: "meta", attrs: { "http-equiv": "Content-Security-Policy", content: contentSecurityPolicy(apiOrigin) }, injectTo: "head-prepend" }];
    },
  };
}
