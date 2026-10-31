// S03d Today (first visit): no cases yet. A welcome, three things to get ready, and how a case works.
import { formatDay, t } from "../../foundation/i18n";
import { hasScreen, screenPath } from "../../foundation/lib/screens";
import { useSession } from "../../foundation/lib/session";
import type { components } from "../../foundation/platform-client/schema";
import { LinkButton, SectionIcon } from "../../foundation/ui";
import { Icon } from "./shared";

const HAND = ["letter", "records", "date"] as const;

/** `ready` is what the API says the person has done; the page counts nothing. */
export function TodayFirst({ ready }: { ready: components["schemas"]["Readiness"] }) {
  const { me } = useSession();
  const name = me.person.greeting_name || me.person.full_name || me.person.email;
  const profilePath = screenPath("profile");
  const items = [
    { key: "account", done: ready.account, to: null },
    { key: "profile", done: ready.profile, to: profilePath },
    { key: "signature", done: ready.signature, to: profilePath },
  ] as const;
  return (
    <div className="av-page av-stack">
      <div>
        <p className="av-muted av-today__date" data-testid="today-date">
          {formatDay(new Date(), me.person.locale ?? undefined, me.person.timezone)}
        </p>
        <h1 className="av-today__title">{t("today.first.title", { name })}</h1>
        <p className="av-muted">{t("today.first.intro")}</p>
      </div>
      <div className="av-today__layout">
        <section aria-labelledby="getting-ready" className="av-card av-stack">
          <SectionIcon glyph="checklist" />
          <div>
            <h2 id="getting-ready">{t("today.first.ready")}</h2>
            <p className="av-muted">{t("today.first.progress", { done: ready.done_steps, total: ready.total_steps })}</p>
          </div>
          <ul className="av-rows">
            {items.map((item, i) => (
              <li key={item.key} className="av-row-item">
                <span className={`av-step-dot${item.done ? " av-step-dot--done" : ""}`} aria-hidden="true">
                  {item.done ? <Icon d="M5 12l5 5 9-10" /> : i + 1}
                </span>
                <div className="av-row-item__text">
                  <p>
                    <strong>{t(`today.first.${item.key}.title`)}</strong>
                  </p>
                  <p className="av-muted">{t(`today.first.${item.key}.body`)}</p>
                </div>
                {item.done ? (
                  <span className="av-badge av-badge--on">{t("today.first.done")}</span>
                ) : (
                  item.to && (
                    <LinkButton to={item.to} variant="secondary">
                      {t(`today.first.${item.key}.action`)}
                    </LinkButton>
                  )
                )}
              </li>
            ))}
          </ul>
          <div className="av-row-item">
            <p className="av-muted av-row-item__text">{t("today.first.can_start")}</p>
            {hasScreen("new_case") && (
              <LinkButton to={screenPath("new_case")!}>
                <Icon d="M12 5v14M5 12h14" />
                {t("today.first.start")}
              </LinkButton>
            )}
          </div>
        </section>
        <aside className="av-today__aside">
          <section aria-labelledby="to-hand" className="av-card av-stack">
            <SectionIcon glyph="folder" />
            <h2 id="to-hand">{t("today.first.hand.title")}</h2>
            <ul className="av-rows">
              {HAND.map((k) => (
                <li key={k} className="av-row-item">
                  <div className="av-row-item__text">
                    <p>
                      <strong>{t(`today.first.hand.${k}.title`)}</strong>
                    </p>
                    <p className="av-muted">{t(`today.first.hand.${k}.body`)}</p>
                  </div>
                </li>
              ))}
            </ul>
          </section>
          <section aria-labelledby="need-hand" className="av-card av-stack">
            <SectionIcon glyph="question" />
            <h2 id="need-hand">{t("today.first.help.title")}</h2>
            <p className="av-muted">{t("today.first.help.body")}</p>
            <LinkButton to="/help" variant="secondary">{t("today.first.help.link")}</LinkButton>
          </section>
        </aside>
      </div>
    </div>
  );
}
