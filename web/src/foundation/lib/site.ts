/** The public home page (site/): set per deployment; under `make dev` it is served at / beside the app. */
export function siteUrl(): string {
  const url = import.meta.env.VITE_SITE_URL || (import.meta.env.DEV ? "/" : "https://www.avodeck.com");
  return url === "/" ? url : url.replace(/\/+$/, "");
}
