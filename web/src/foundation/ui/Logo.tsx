import { t } from "../i18n";

/** The Avodeck mark and name (docs/design/screens/Logo6.dc.html). */
export function Logo() {
  return (
    <span className="av-logo">
      <svg width="32" height="32" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
        <rect width="32" height="32" rx="8" fill="currentColor" />
        <path d="M9 22 L16 9 L23 22" fill="none" stroke="#FFFFFF" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M12 18 H20" stroke="#7CCFA6" strokeWidth="3" strokeLinecap="round" />
      </svg>
      <span>{t("app.name")}</span>
    </span>
  );
}
