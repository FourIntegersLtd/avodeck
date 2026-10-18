/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** The API origin for this cell, e.g. https://uk.api.avodeck.com. Empty: same origin (local, proxied). */
  readonly VITE_API_ORIGIN?: string;
  /** The public home page the logo links to, e.g. https://www.avodeck.com. Empty: localhost:3000 in development. */
  readonly VITE_SITE_URL?: string;
  /** Support phone number shown on the sign-in screen; omitted when empty. */
  readonly VITE_SUPPORT_PHONE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
