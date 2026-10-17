import type { ReactNode } from "react";
import { t } from "../../foundation/i18n";
import { Logo } from "../../foundation/ui";

/** S01's frame: what Avodeck promises on the left, one task on the right. */
export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="av-sign-in">
      <aside className="av-sign-in__brand">
        <Logo />
        <h2 className="av-sign-in__tagline">{t("sign_in.brand.tagline")}</h2>
        <ul className="av-sign-in__points">
          <li>{t("sign_in.brand.point_pages")}</li>
          <li>{t("sign_in.brand.point_visits")}</li>
          <li>{t("sign_in.brand.point_opinion")}</li>
        </ul>
      </aside>
      <main className="av-sign-in__main" id="main">
        <div className="av-sign-in__form av-stack">{children}</div>
      </main>
    </div>
  );
}
