import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { t } from "../i18n";
import { Logo } from "./Logo";

/** The signed-in frame: skip link, logo, the two main places, and the page. */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <>
      <a className="av-skip" href="#main">
        {t("app.skip_to_content")}
      </a>
      <header className="av-shell__header">
        <div className="av-shell__bar">
          <NavLink to="/today" className="av-logo">
            <Logo />
          </NavLink>
          <nav className="av-shell__nav" aria-label={t("app.nav.label")}>
            <ul>
              <li>
                <NavLink to="/today">{t("app.nav.today")}</NavLink>
              </li>
              <li>
                <NavLink to="/account">{t("app.nav.account")}</NavLink>
              </li>
            </ul>
          </nav>
        </div>
      </header>
      <main id="main" tabIndex={-1}>
        {children}
      </main>
    </>
  );
}
