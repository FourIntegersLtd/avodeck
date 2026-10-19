// Help (the header's "Help"): who to call, from the cell.
import { t } from "../../foundation/i18n";
import { SupportLine } from "../../foundation/ui";

export function Help() {
  return (
    <div className="av-page av-page--narrow av-stack">
      <h1>{t("help.title")}</h1>
      <p>{t("help.intro")}</p>
      <SupportLine />
    </div>
  );
}
