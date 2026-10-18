/** The public home page (site/): set per deployment, localhost:3000 under `make dev`. */
export function siteUrl(): string {
  return (import.meta.env.VITE_SITE_URL || (import.meta.env.DEV ? "http://localhost:3000" : "https://www.avodeck.com")).replace(/\/+$/, "");
}
