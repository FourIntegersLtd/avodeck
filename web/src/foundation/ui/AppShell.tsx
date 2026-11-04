import * as RadixDialog from "@radix-ui/react-dialog";
import { useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useMatch } from "react-router-dom";
import { prefetchRoute } from "../lib/routePrefetch";
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
  /** Support staff get the console in the main places; the server still decides access. */
  support?: boolean;
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

/** Small line icons for the menu rows (decorative: the words carry the meaning). Same 24px, stroke-2 style as the bell and hamburger. */
const NAV_ICONS: Record<string, string[]> = {
  today: ["M4 11l8-7 8 7", "M6 10v10h12V10"],
  cases: ["M4 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"],
  profile: ["M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M4 21c0-4 4-6.5 8-6.5s8 2.5 8 6.5"],
  support: ["M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"],
  help: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17h.01"],
};
const FALLBACK_ICON = NAV_ICONS.today!;

function NavIcon({ name }: { name: string }) {
  return (
    <svg className="av-shell__nav-icon" width="24" height="24" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      {(NAV_ICONS[name] ?? FALLBACK_ICON).map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}

/** The places (today/cases/profile/support console): one list, drawn by both the header nav and the drawer. */
function ShellNavLinks({
  nav,
  current,
  onNavigate,
  icons,
  extra,
}: {
  nav: Array<readonly [string, string]>;
  current: string | null;
  onNavigate?: () => void;
  /** Draw a leading icon on each row (the drawer does; the desktop bar does not). */
  icons?: boolean;
  /** More rows after the places (Help in the drawer). */
  extra?: ReactNode;
}) {
  return (
    <ul>
      {nav.map(([key, to]) => (
        <li key={key}>
          <Link
            to={to}
            aria-current={key === current ? "page" : undefined}
            onClick={onNavigate}
            onMouseEnter={() => prefetchRoute(to)}
            onFocus={() => prefetchRoute(to)}
            onTouchStart={() => prefetchRoute(to)}
          >
            {icons && <NavIcon name={key} />}
            {t(`app.nav.${key}`)}
          </Link>
        </li>
      ))}
      {extra}
    </ul>
  );
}

/** The signed-in frame (S03 header): skip link, logo, main places, the bell, the person, and the
 * page. The main places show as the header's own nav on a wide screen; below the breakpoint they
 * hide and a hamburger opens the same places in a drawer instead (U2: no cramped wrapping nav). */
export function AppShell({ children, name, support }: AppShellProps) {
  const messages = useMessages();
  const unread = messages?.unread ?? 0;
  // Places slices register (screen keys "cases", "profile", "help"); hidden until a slice provides them.
  const places = (support ? [] : (["cases", "profile"] as const)).flatMap((key) => {
    const to = screenPath(key);
    return to ? [[key, to] as const] : [];
  });
  const help = screenPath("help");
  const consolePath = support ? screenPath("support_console") : null;
  // Support staff get the console only: no expert places (Today, Cases, Your profile), no Messages bell.
  const nav: Array<readonly [string, string]> = support
    ? consolePath ? [["support", consolePath] as const] : []
    : [["today", "/today"], ...places];
  const current = currentPlace(nav, useLocation());
  // A case screen has its own single bar (logo, case, steps, avatar) from the case frame.
  const inCase = useMatch("/cases/:caseId/*") !== null;
  // Always on for support staff, case screens too, so nobody mistakes this for an expert's account.
  const band = support ? (
    <div className="av-shell__support-band" role="note">
      {t("app.badge.support_account")}
    </div>
  ) : null;

  const [drawerOpen, setDrawerOpen] = useState(false);
  const hamburgerRef = useRef<HTMLButtonElement>(null);
  const closeDrawer = () => setDrawerOpen(false);

  return (
    <>
      <a className="av-skip" href="#main">
        {t("app.skip_to_content")}
      </a>
      {inCase && band}
      {!inCase && (
      <header className="av-shell__header">
        {band}
        <div className="av-shell__bar">
          <div className="av-shell__left">
            <button
              ref={hamburgerRef}
              type="button"
              className="av-shell__hamburger"
              aria-expanded={drawerOpen}
              aria-controls="av-shell-drawer"
              aria-label={drawerOpen ? t("app.nav.close_menu") : t("app.nav.open_menu")}
              onClick={() => setDrawerOpen((o) => !o)}
            >
              <svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 6h18M3 12h18M3 18h18" />
              </svg>
            </button>
            <Logo />
            <nav className="av-shell__nav" aria-label={t("app.nav.label")}>
              <ShellNavLinks nav={nav} current={current} />
            </nav>
          </div>
          <div className="av-shell__right">
            {!support && (
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
            )}
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
      {!inCase && (
        <RadixDialog.Root open={drawerOpen} onOpenChange={setDrawerOpen}>
          <RadixDialog.Portal>
            <RadixDialog.Overlay className="av-shell__drawer-backdrop">
              <button type="button" className="av-shell__drawer-scrim" aria-label={t("app.nav.close_menu")} onClick={closeDrawer} />
              <RadixDialog.Content id="av-shell-drawer" className="av-shell__drawer" aria-describedby={undefined} onCloseAutoFocus={(e) => { e.preventDefault(); hamburgerRef.current?.focus(); }}>
                <RadixDialog.Title className="av-visually-hidden">{t("app.nav.menu")}</RadixDialog.Title>
                <div className="av-shell__drawer-head">
                  <button type="button" className="av-shell__drawer-close" aria-label={t("app.nav.close_menu")} onClick={closeDrawer}>
                    <svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M6 6l12 12M18 6L6 18" />
                    </svg>
                  </button>
                </div>
                <Link to="/account" className="av-shell__drawer-account" onClick={closeDrawer}>
                  <span className="av-shell__avatar-disc" aria-hidden="true">
                    {initials(name)}
                  </span>
                  <span className="av-shell__drawer-who">
                    {name && <span className="av-shell__drawer-name">{name}</span>}
                    <span className="av-shell__drawer-caption">{t("app.nav.account")}</span>
                  </span>
                </Link>
                <nav className="av-shell__drawer-nav av-shell__nav" aria-label={t("app.nav.label")}>
                  <ShellNavLinks
                    nav={nav}
                    current={current}
                    onNavigate={closeDrawer}
                    icons
                    extra={
                      help ? (
                        <li>
                          <Link to={help} onClick={closeDrawer}>
                            <NavIcon name="help" />
                            {t("app.nav.help")}
                          </Link>
                        </li>
                      ) : null
                    }
                  />
                </nav>
              </RadixDialog.Content>
            </RadixDialog.Overlay>
          </RadixDialog.Portal>
        </RadixDialog.Root>
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
