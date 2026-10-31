import { Link } from "react-router-dom";
import { t } from "../i18n";
import { siteUrl } from "../lib/site";

const PEAR = "M50 12 C66 12 74 27 76 40 C78 53 88 61 88 80 C88 101 71 114 50 114 C29 114 12 101 12 80 C12 61 22 53 24 40 C26 27 34 12 50 12 Z";

/** Where the mark goes: the signed-in home, the marketing site, or nowhere (a bar that is itself a link). */
type Destination = "app" | "site" | "none";

/**
 * The Avodeck mark and name (docs/design/screens/Logo6.dc.html), 40px tall, linking to the home page.
 * The name is drawn inside the SVG, so no extra font has to load. `onDark` is for the dark panel.
 *
 * `to` defaults to the signed-in home: a signed-in person clicking the logo expects the app, not the
 * marketing site. Only the sign-in screen passes "site".
 */
export function Logo({ onDark = false, to = "app" }: { onDark?: boolean; to?: Destination }) {
  const [skin, flesh, pit] = onDark ? ["#E6F2EC", "#2E7556", "#E6F2EC"] : ["#0F3D2C", "#E4F3D6", "#1E6B4E"];
  const className = onDark ? "av-logo av-logo--on-dark" : "av-logo";
  const mark = (
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
  );
  if (to === "none") return <span className={className}>{mark}</span>;
  if (to === "site") {
    return (
      <a href={siteUrl()} className={className} aria-label={t("app.logo_home")}>
        {mark}
      </a>
    );
  }
  return (
    <Link to="/" className={className} aria-label={t("app.logo_home")}>
      {mark}
    </Link>
  );
}
