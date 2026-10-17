import { t } from "../foundation/i18n";
import { LinkButton } from "../foundation/ui";

export function NotFound() {
  return (
    <main className="av-page av-stack" id="main">
      <h1>{t("app.not_found.title")}</h1>
      <p>{t("app.not_found.body")}</p>
      <LinkButton to="/today">{t("app.not_found.action")}</LinkButton>
    </main>
  );
}
