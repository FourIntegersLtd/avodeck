import { t } from "../i18n";
import { useResource } from "../lib/useResource";
import { api, unwrap } from "../platform-client";

/** "Need help? Call us on …": the cell's support number and hours (GET /v1/cell). Nothing when it has no number. */
export function SupportLine() {
  const cell = useResource(() => unwrap(api.GET("/v1/cell")), []);
  const phone = cell.data?.support_phone;
  if (!phone) return null;
  const hours = cell.data?.support_hours;
  const [before, after] = t("sign_in.help", { phone: "\u0000", hours: hours ? `, ${hours}` : "" }).split("\u0000");
  return (
    <p className="av-muted" data-testid="support-line">
      {before}
      <a href={`tel:${phone.replace(/[^+\d]/g, "")}`}>{phone}</a>
      {after}
    </p>
  );
}
