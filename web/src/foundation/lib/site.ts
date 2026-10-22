import { isDevelopment, siteUrlSetting } from "./env";

/** The public home page (site/): set per deployment, localhost:3000 under `make dev`. */
export function siteUrl(): string {
  return (siteUrlSetting || (isDevelopment() ? "http://localhost:3000" : "https://www.avodeck.com")).replace(/\/+$/, "");
}
