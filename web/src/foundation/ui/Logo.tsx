import { t } from "../i18n";

const PEAR = "M50 12 C66 12 74 27 76 40 C78 53 88 61 88 80 C88 101 71 114 50 114 C29 114 12 101 12 80 C12 61 22 53 24 40 C26 27 34 12 50 12 Z";

/** The Avodeck mark and name (docs/design/screens/Logo6.dc.html): light colours on the dark panel. */
export function Logo({ onDark = false }: { onDark?: boolean }) {
  const [skin, flesh, pit] = onDark ? ["#E6F2EC", "#2E7556", "#E6F2EC"] : ["#0F3D2C", "#E4F3D6", "#1E6B4E"];
  return (
    <span className={onDark ? "av-logo av-logo--on-dark" : "av-logo"}>
      <svg width="24.4" height="32" viewBox="8 8 84 110" aria-hidden="true" focusable="false">
        <path d={PEAR} fill={skin} />
        <path d={PEAR} transform="translate(50 76) scale(0.80) translate(-50 -76)" fill={flesh} />
        <circle cx="50" cy="83" r="17" fill={pit} />
      </svg>
      <span>{t("app.name")}</span>
    </span>
  );
}
