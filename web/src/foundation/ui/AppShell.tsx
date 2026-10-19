import type { ReactNode } from "react";
import { Link, useLocation, useMatch } from "react-router-dom";
import { t } from "../i18n";
import { useMessages } from "../lib/messages";
import { screenPath } from "../lib/screens";
import { initials } from "./initials";
import { Logo } from "./Logo";
import { Notice } from "./Notice";

export interface AppShellProps {
  children: ReactNode;
  /** The signed-in person's name, for the initials that open their account. */
  name?: string | null;
}

/**
 * The one place to highlight: a place whose address has a #part wins when you are on exactly that
 * part (Cases is "/today#your-cases"); otherwise the first place whose page you are on (Today).
 */
function currentPlace(nav: Array<readonly [string, string]>, at: { pathname: string; hash: string }): string | null {
  const on = (to: string) => {
    const [path = "", hash] = to.split("#");
    const samePage = at.pathname === path || at.pathname.startsWith(`${path}/`);
    return { samePage, hash: hash ? `#${hash}` : "" };
  };
  const exact = nav.find(([, to]) => {
    const place = on(to);
    return place.samePage && place.hash !== "" && place.hash === at.hash;
  });
  if (exact) return exact[0];
  return nav.find(([, to]) => on(to).samePage && on(to).hash === "")?.[0] ?? null;
}

/** The signed-in frame (S03 header): skip link, logo, main places, the bell, the person, and the page. */
export function AppShell({ children, name }: AppShellProps) {
  const messages = useMessages();
  const unread = messages?.unread ?? 0;
  // Places slices register (screen keys "cases", "profile", "help"); hidden until a slice provides them.
  const places = (["cases", "profile"] as const).flatMap((key) => {
    const to = screenPath(key);
    return to ? [[key, to] as const] : [];
  });
  const help = screenPath("help");
  const nav: Array<readonly [string, string]> = [["today", "/today"], ...places];
  const current = currentPlace(nav, useLocation());
  // A case screen has its own single bar (logo, case, steps, avatar) from the case frame.
  const inCase = useMatch("/cases/:caseId/*") !== null;
  return (
    <>
      <a className="av-skip" href="#main">
        {t("app.skip_to_content")}
      </a>
      {!inCase && (
      <header className="av-shell__header">
        <div className="av-shell__bar">
          <div className="av-shell__left">
            <Logo />
            <nav className="av-shell__nav" aria-label={t("app.nav.label")}>
              <ul>
                {nav.map(([key, to]) => (
                  <li key={key}>
                    <Link to={to} aria-current={key === current ? "page" : undefined}>
                      {t(`app.nav.${key}`)}
                    </Link>
                  </li>
                ))}
              </ul>
            </nav>
          </div>
          <div className="av-shell__right">
            <Link to="/messages" className="av-shell__bell" aria-label={unread ? t("app.nav.messages_unread", { count: unread }) : t("app.nav.messages")}>
              <svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
                <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
              </svg>
              {unread > 0 && (
                <span className="av-shell__count" aria-hidden="true">
                  {unread > 99 ? "99+" : unread}
                </span>
              )}
            </Link>
            {help && (
              <Link to={help} className="av-shell__help">
                {t("app.nav.help")}
              </Link>
            )}
            <Link to="/account" className="av-shell__avatar">
              <span className="av-shell__avatar-disc" aria-hidden="true">
                {initials(name)}
              </span>
              <span className="av-visually-hidden">{t("app.nav.account")}</span>
            </Link>
          </div>
        </div>
      </header>
      )}
      <main id="main" tabIndex={-1}>
        {messages?.bounced && (
          <div className="av-page av-shell__banner">
            <Notice tone="warning">
              <p>
                {t("app.bounce.body")} <Link to="/account">{t("app.bounce.action")}</Link>
              </p>
            </Notice>
          </div>
        )}
        {children}
      </main>
    </>
  );
}
