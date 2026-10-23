import type { ReactNode } from "react";
import { t } from "../i18n";
import { Logo } from "./Logo";

// One line icon per promise (stroke paths on a 24px grid).
const POINTS = [
  { key: "sign_in.brand.point_pages", icon: "M8 3h9a2 2 0 0 1 2 2v12M5 7h9a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2zM7 12h5M7 16h5" },
  { key: "sign_in.brand.point_facts", icon: "M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1" },
  { key: "sign_in.brand.point_visits", icon: "M5 5h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zM3 10h18M8 3v4M16 3v4M8 15l2.5 2.5L16 13" },
  { key: "sign_in.brand.point_court", icon: "M12 3v18M7 21h10M5 7h14M5 7l-3 7a3 3 0 0 0 6 0zM19 7l-3 7a3 3 0 0 0 6 0z" },
] as const;

function Icon({ d, size = 24 }: { d: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d={d} />
    </svg>
  );
}

/** S01's frame (canvas board S01-SignIn): the promise on the left, one task on the right. */
export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="av-sign-in">
      <aside className="av-sign-in__brand">
        <Logo onDark />
        <div className="av-sign-in__promise">
          <h2 className="av-sign-in__tagline">{t("sign_in.brand.tagline")}</h2>
          <ul className="av-sign-in__points">
            {POINTS.map(({ key, icon }) => (
              <li key={key}>
                <span className="av-sign-in__icon">
                  <Icon d={icon} />
                </span>
                <span>{t(key)}</span>
              </li>
            ))}
          </ul>
        </div>
        <p className="av-sign-in__footnote">
          <Icon d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z" size={20} />
          <span>{t("sign_in.brand.point_opinion")}</span>
        </p>
      </aside>
      <main className="av-sign-in__main" id="main">
        <div className="av-sign-in__form av-stack">{children}</div>
      </main>
    </div>
  );
}
