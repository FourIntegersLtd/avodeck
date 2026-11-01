import { useEffect, useRef, useState, type ReactNode } from "react";
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

/** The places (today/cases/profile/support console): one list, drawn by both the header nav and the drawer. */
function ShellNavLinks({
  nav,
  current,
  onNavigate,
}: {
  nav: Array<readonly [string, string]>;
  current: string | null;
  onNavigate?: () => void;
}) {
  return (
    <ul>
      {nav.map(([key, to]) => (
        <li key={key}>
          <Link to={to} aria-current={key === current ? "page" : undefined} onClick={onNavigate}>
            {t(`app.nav.${key}`)}
          </Link>
        </li>
      ))}
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
  const drawerRef = useRef<HTMLDivElement>(null);
  const closeDrawer = () => setDrawerOpen(false);

  // The drawer is an overlay, not a push: lock the page behind it so a phone can't scroll it.
  useEffect(() => {
    document.body.style.overflow = drawerOpen ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [drawerOpen]);

  // Focus moves into the drawer when it opens, Escape closes it and gives focus back to the
  // hamburger, and Tab is trapped inside it while it is open (U2: a phone-confident focus order).
  useEffect(() => {
    if (!drawerOpen) return;
    const node = drawerRef.current;
    const focusable = node ? Array.from(node.querySelectorAll<HTMLElement>("a[href], button:not([disabled])")) : [];
    (focusable[0] ?? node)?.focus();
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setDrawerOpen(false);
        hamburgerRef.current?.focus();
        return;
      }
      if (e.key !== "Tab" || focusable.length === 0) return;
      const first = focusable[0]!;
      const last = focusable[focusable.length - 1]!;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [drawerOpen]);

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
      {!inCase && drawerOpen && (
        <div className="av-shell__drawer-backdrop">
          <button type="button" className="av-shell__drawer-scrim" aria-label={t("app.nav.close_menu")} onClick={closeDrawer} />
          <div id="av-shell-drawer" ref={drawerRef} className="av-shell__drawer" role="dialog" aria-modal="true" aria-label={t("app.nav.menu")}>
            <div className="av-shell__drawer-head">
              <button type="button" className="av-shell__drawer-close" aria-label={t("app.nav.close_menu")} onClick={closeDrawer}>
                <svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </div>
            <nav className="av-shell__drawer-nav av-shell__nav" aria-label={t("app.nav.label")}>
              <ShellNavLinks nav={nav} current={current} onNavigate={closeDrawer} />
            </nav>
          </div>
        </div>
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
