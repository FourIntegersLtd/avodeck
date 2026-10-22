// Build settings, read the way both bundlers allow: Next inlines `process.env.NEXT_PUBLIC_*`
// written out in full, and Vitest supplies `process.env.NODE_ENV`.
// MODE is what Vitest's stubEnv sets in tests; Next never defines it, so the build falls back to NODE_ENV.
const mode = () => process.env.MODE ?? process.env.NODE_ENV;
/** True in `next dev` and under tests; false in a production build (where dev-only helpers are absent). */
export const isDevelopment = () => mode() !== "production";
/** True while Vitest runs. */
export const isTest = () => mode() === "test";
/** The API's origin when it is not the page's own (empty: same origin). */
export const apiOriginSetting = process.env.NEXT_PUBLIC_API_ORIGIN ?? "";
/** The public home page's address, set per deployment (empty: use the default). */
export const siteUrlSetting = process.env.NEXT_PUBLIC_SITE_URL ?? "";
