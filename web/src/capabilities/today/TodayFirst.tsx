// S03d Today (first visit): no cases yet. A welcome, three things to get ready, and how a case works.
import { formatDay, t } from "../../foundation/i18n";
import { hasScreen, screenPath } from "../../foundation/lib/screens";
import { useSession } from "../../foundation/lib/session";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap } from "../../foundation/platform-client";
import { LinkButton } from "../../foundation/ui";
import { Icon } from "./shared";

const STEPS = [1, 2, 3, 4, 5] as const;
const HAND = ["letter", "records", "date"] as const;

/** What the person has already done, from the profile and signature endpoints. A failed look counts as not done. */
function useReadiness() {
  const { me } = useSession();
  const found = useResource(async () => {
    const profile = await unwrap(api.GET("/v1/me/profile")).catch(() => null);
    const signature = await unwrap(api.GET("/v1/me/signature/image", { parseAs: "blob" })).then(() => true, () => false);
    return { profile, signature };
  }, []);
  const p = found.data?.profile;
  return {
    loaded: Boolean(found.data),
    account: me.mfa_enrolled,
    profile: Boolean(p?.profession && p.registration_number && p.specialties.length > 0),
    signature: Boolean(found.data?.signature),
  };
}

export function TodayFirst() {
  const { me } = useSession();
  const ready = useReadiness();
  const name = me.person.greeting_name || me.person.full_name || me.person.email;
  const profilePath = screenPath("profile");
  const done = [ready.account, ready.profile, ready.signature].filter(Boolean).length;
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
          <div>
            <h2 id="getting-ready">{t("today.first.ready")}</h2>
            {ready.loaded && <p className="av-muted">{t("today.first.progress", { done, total: items.length })}</p>}
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
            <h2 id="need-hand">{t("today.first.help.title")}</h2>
            <p className="av-muted">{t("today.first.help.body")}</p>
            <LinkButton to="/help" variant="secondary">{t("today.first.help.link")}</LinkButton>
          </section>
        </aside>
      </div>
      <section aria-labelledby="how-works" className="av-section av-stack">
        <div>
          <h2 id="how-works">{t("today.first.how.title")}</h2>
          <p className="av-muted">{t("today.first.how.intro")}</p>
        </div>
        <ol className="av-how">
          {STEPS.map((n) => (
            <li key={n} className="av-card av-how__step">
              <p>
                <strong>
                  {n} {t(`today.first.how.${n}.title`)}
                </strong>
              </p>
              <p className="av-muted">{t(`today.first.how.${n}.body`)}</p>
            </li>
          ))}
        </ol>
        <p>
          <strong>{t("today.first.never.lead")}</strong> {t("today.first.never.body")}
        </p>
      </section>
    </div>
  );
}
