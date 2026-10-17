/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** The API origin for this cell, e.g. https://uk.api.avodeck.com. Empty: same origin (local, proxied). */
  readonly VITE_API_ORIGIN?: string;
  /** Support phone number shown on the sign-in screen; omitted when empty. */
  readonly VITE_SUPPORT_PHONE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
