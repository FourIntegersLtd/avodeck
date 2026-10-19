import { t } from "../i18n";
import { siteUrl } from "../lib/site";

const PEAR = "M50 12 C66 12 74 27 76 40 C78 53 88 61 88 80 C88 101 71 114 50 114 C29 114 12 101 12 80 C12 61 22 53 24 40 C26 27 34 12 50 12 Z";

/**
 * The Avodeck mark and name (docs/design/screens/Logo6.dc.html), 40px tall, linking to the home page.
 * The name is drawn inside the SVG, so no extra font has to load. `onDark` is for the dark panel.
 */
export function Logo({ onDark = false }: { onDark?: boolean }) {
  const [skin, flesh, pit] = onDark ? ["#E6F2EC", "#2E7556", "#E6F2EC"] : ["#0F3D2C", "#E4F3D6", "#1E6B4E"];
  return (
    <a href={siteUrl()} className={onDark ? "av-logo av-logo--on-dark" : "av-logo"} aria-label={t("app.logo_home")}>
      <svg width="134" height="40" viewBox="0 0 134 40" aria-hidden="true" focusable="false">
        <g transform="scale(0.3636) translate(-8 -8)">
          <path d={PEAR} fill={skin} />
          <path d={PEAR} transform="translate(50 76) scale(0.80) translate(-50 -76)" fill={flesh} />
          <circle cx="50" cy="83" r="17" fill={pit} />
        </g>
        <text x="40" y="28" fontFamily="Atkinson Hyperlegible, system-ui, sans-serif" fontSize="25" fontWeight="700" letterSpacing="-0.6" fill="currentColor">
          {t("app.name")}
        </text>
      </svg>
    </a>
  );
}
