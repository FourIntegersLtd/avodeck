import { t } from "../i18n";
import { siteUrl } from "../lib/site";

const PEAR = "M50 12 C66 12 74 27 76 40 C78 53 88 61 88 80 C88 101 71 114 50 114 C29 114 12 101 12 80 C12 61 22 53 24 40 C26 27 34 12 50 12 Z";

/**
 * The Avodeck mark and name (canvas Logo6 and the board headers): the pear as an SVG beside the name in
 * Outfit. `large` is the sign-in size (48px mark); `onDark` is for the dark panel.
 */
export function Logo({ onDark = false, large = false }: { onDark?: boolean; large?: boolean }) {
  const [skin, flesh, pit] = onDark ? ["#E6F2EC", "#2E7556", "#E6F2EC"] : ["#0F3D2C", "#E4F3D6", "#1E6B4E"];
  const classes = ["av-logo", onDark && "av-logo--on-dark", large && "av-logo--large"].filter(Boolean).join(" ");
  return (
    <a href={siteUrl()} className={classes} aria-label={t("app.logo_home")}>
      <svg viewBox="8 8 84 110" aria-hidden="true" focusable="false">
        <path d={PEAR} fill={skin} />
        <path d={PEAR} transform="translate(50 76) scale(0.80) translate(-50 -76)" fill={flesh} />
        <circle cx="50" cy="83" r="17" fill={pit} />
      </svg>
      <span className="av-logo__word">{t("app.name")}</span>
    </a>
  );
}
