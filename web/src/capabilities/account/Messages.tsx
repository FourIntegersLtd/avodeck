// Messages: what the bell opens. Each kind's words come from the language files
// (`notifications.kind.<kind>`, which a slice registers); unknown kinds get plain words.
import { useState } from "react";
import { Link } from "react-router-dom";
import { formatRelative, hasMessage, t } from "../../foundation/i18n";
import { useMessages } from "../../foundation/lib/messages";
import { useSession } from "../../foundation/lib/session";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, LoadError, Loading } from "../../foundation/ui";

type Message = Schemas["Notification"];

function words(m: Message): string {
  const key = `notifications.kind.${m.kind}`;
  const params = { case: m.case_label ?? "" };
  if (hasMessage(key)) return t(key, params);
  return m.case_label ? t("messages.fallback", params) : t("messages.fallback_no_case");
}

export function Messages() {
  const { me } = useSession();
  const summary = useMessages();
  const found = useResource(() => unwrap(api.GET("/v1/notifications")), [], { cache: "account/Messages#Messages:found" });
  const [read, setRead] = useState<Record<string, Message>>({});
  const markRead = async (id: string) => {
    const done = await unwrap(api.POST("/v1/notifications/{notification_id}/read", { params: { path: { notification_id: id } } }));
    setRead((r) => ({ ...r, [id]: done }));
    summary?.reload();
  };
  const items = found.data?.items.map((m) => read[m.id] ?? m);
  return (
    <div className="av-page av-stack">
      <h1 id="messages-title">{t("messages.title")}</h1>
      {found.error && <LoadError onRetry={found.reload} />}
      {!found.data && !found.error && <Loading />}
      {items && items.length === 0 && <p>{t("messages.none")}</p>}
      {items && items.length > 0 && (
        <ul className="av-card av-rows" aria-labelledby="messages-title" role="list">
          {items.map((m) => (
            <li key={m.id} className="av-row-item">
              <div>
                <p className={m.read_at ? undefined : "av-field__label"}>
                  {!m.read_at && <span className="av-badge av-badge--look">{t("messages.new")}</span>} {words(m)}
                </p>
                <p className="av-muted">{formatRelative(new Date(m.created_at), me.person.locale)}</p>
              </div>
              <div className="av-row">
                {m.case_id && m.case_label && <Link to={`/cases/${m.case_id}`}>{t("messages.open", { case: m.case_label })}</Link>}
                {!m.read_at && (
                  <Button variant="secondary" onClick={() => markRead(m.id)}>
                    {t("messages.mark_read")}
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
