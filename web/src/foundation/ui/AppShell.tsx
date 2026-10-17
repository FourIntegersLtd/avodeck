import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { t } from "../i18n";
import { useMessages } from "../lib/messages";
import { Logo } from "./Logo";
import { Notice } from "./Notice";

export interface AppShellProps {
  children: ReactNode;
  /** The signed-in person's name, for the initials that open their account. */
  name?: string | null;
}

function initials(name: string | null | undefined): string {
  const words = (name ?? "").replace(/^(dr|mr|mrs|ms|miss|prof)\.?\s+/i, "").split(/\s+/).filter(Boolean);
  if (!words.length) return "";
  return (words[0]![0]! + (words.length > 1 ? words[words.length - 1]![0]! : "")).toUpperCase();
}

/** The signed-in frame (S03 header): skip link, logo, main places, the bell, the person, and the page. */
export function AppShell({ children, name }: AppShellProps) {
  const messages = useMessages();
  const unread = messages?.unread ?? 0;
  return (
    <>
      <a className="av-skip" href="#main">
        {t("app.skip_to_content")}
      </a>
      <header className="av-shell__header">
        <div className="av-shell__bar">
          <div className="av-shell__left">
            <NavLink to="/today" className="av-logo">
              <Logo />
            </NavLink>
            <nav className="av-shell__nav" aria-label={t("app.nav.label")}>
              <ul>
                <li>
                  <NavLink to="/today">{t("app.nav.today")}</NavLink>
                </li>
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
            <Link to="/account" className="av-shell__avatar" aria-label={t("app.nav.account")}>
              <span aria-hidden="true">{initials(name)}</span>
            </Link>
          </div>
        </div>
      </header>
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
