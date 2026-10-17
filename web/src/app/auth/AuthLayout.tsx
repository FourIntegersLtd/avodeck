import type { ReactNode } from "react";
import { t } from "../../foundation/i18n";
import { Logo } from "../../foundation/ui";

const POINTS = ["sign_in.brand.point_pages", "sign_in.brand.point_visits", "sign_in.brand.point_court"] as const;

/** S01's frame (canvas board S01-SignIn): the promise on the left, one task on the right. */
export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="av-sign-in">
      <aside className="av-sign-in__brand">
        <Logo onDark />
        <div className="av-sign-in__promise">
          <h2 className="av-sign-in__tagline">{t("sign_in.brand.tagline")}</h2>
          <ul className="av-sign-in__points">
            {POINTS.map((key) => (
              <li key={key}>
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#7CCFA6" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
                  <path d="M5 12l5 5 9-10" />
                </svg>
                <span>{t(key)}</span>
              </li>
            ))}
          </ul>
        </div>
        <p className="av-sign-in__footnote">{t("sign_in.brand.point_opinion")}</p>
      </aside>
      <main className="av-sign-in__main" id="main">
        <div className="av-sign-in__form av-stack">{children}</div>
      </main>
    </div>
  );
}
