// S03a Today: what needs you, the week ahead, and where to pick up. The case list is on Cases (S03c).
import { Link } from "react-router-dom";
import { formatDay, hourIn, t } from "../../foundation/i18n";
import { hasScreen, screenPath } from "../../foundation/lib/screens";
import { useSession } from "../../foundation/lib/session";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { LinkButton, LoadError, Loading, SectionIcon, stepLabel } from "../../foundation/ui";
import { NeedsYou } from "./NeedsYou";
import { TodayFirst } from "./TodayFirst";
import { Icon, StepRing, caseHref, usePacks, type CaseCard } from "./shared";

function greetingKey(hour: number) {
  return hour < 12 ? "today.greeting.morning" : hour < 18 ? "today.greeting.afternoon" : "today.greeting.evening";
}

export function Today() {
  const { me } = useSession();
  const today = useResource(() => unwrap(api.GET("/v1/today")), [], { cache: "today/Today#Today:today" });
  const name = me.person.greeting_name || me.person.full_name || me.person.email;
  const data = today.data;
  // No cases at all, open or closed: the first-visit welcome (S03d).
  if (data && data.cases.length === 0 && data.closed_count === 0) return <TodayFirst ready={data.readiness} />;
  return (
    <div className="av-page">
      <div className="av-today__head">
        <div>
          <p className="av-muted av-today__date" data-testid="today-date">
            {formatDay(new Date(), me.person.locale ?? undefined, me.person.timezone)}
          </p>
          <h1 className="av-today__title">{t(greetingKey(hourIn(me.person.timezone)), { name })}</h1>
          {data && <p className="av-muted">{intro()}</p>}
        </div>
      </div>
      {today.error && <LoadError onRetry={today.reload} />}
      {!data && !today.error && <Loading />}
      {data && <Calm data={data} timezone={me.person.timezone} locale={me.person.locale} />}
    </div>
  );
}

/** "… All your cases are under Cases." with Cases a link. */
function intro() {
  const shape = t("today.calm.intro", { link: "\u0001" });
  const [before = "", after = ""] = shape.split("\u0001");
  const to = screenPath("cases");
  return (
    <>
      {before}
      {to ? <Link to={to}>{t("today.calm.cases_link")}</Link> : t("today.calm.cases_link")}
      {after}
    </>
  );
}

function Calm({ data, timezone, locale }: { data: Schemas["Today"]; timezone: string | null; locale: string | null }) {
  const hasNeeds = data.needs_you.length > 0;
  const packs = usePacks(data.cases);
  const ranked = data.needs_you; // the API ranks them
  const pick = data.cases.find((c) => c.case_id === ranked.find((n) => n.case_id)?.case_id) ?? data.cases[0];
  const cases = screenPath("cases") ?? "/cases";
  return (
    <div className="av-today__layout">
      <div className="av-today__main">
        <NeedsYou items={data.needs_you} cases={data.cases} />
        <Week cases={data.cases} needs={data.needs_you} timezone={timezone} locale={locale} />
      </div>
      <aside className="av-today__aside">
        {pick && (
          <section aria-labelledby="pick-up" className="av-card av-stack">
            <SectionIcon glyph="folder" />
            <h2 id="pick-up">{t("today.pickup.title")}</h2>
            <div className="av-pickup">
              {pick.stage && packs.data?.get(`${pick.jurisdiction}/${pick.case_type}/${pick.specialty}`) && (
                <PickRing card={pick} pack={packs.data.get(`${pick.jurisdiction}/${pick.case_type}/${pick.specialty}`)!} />
              )}
              <p className="av-pickup__text">
                <strong>{pick.label}</strong>
                {pick.stage && <span className="av-muted"> {stepLabel(pick.stage)}</span>}
              </p>
            </div>
            <LinkButton to={caseHref(pick.case_id)} variant="secondary">
              {t("today.pickup.open")}
            </LinkButton>
          </section>
        )}
        <section aria-labelledby="glance" className="av-card av-stack">
          <h2 id="glance">{t("today.glance.title")}</h2>
          <p className="av-glance">
            <span>{t("today.glance.open")}</span>
            <strong>{data.open_count}</strong>
          </p>
          <p className="av-glance">
            <span>{t("today.glance.due_month")}</span>
            <strong>{data.due_this_month}</strong>
          </p>
          <Link to={cases}>{t("today.glance.all")}</Link>
        </section>
        {hasScreen("new_case") && (
          <LinkButton to={screenPath("new_case")!} variant={hasNeeds ? "secondary" : "primary"} className="av-today__new">
            <Icon d="M12 5v14M5 12h14" />
            {t("today.new.start")}
          </LinkButton>
        )}
      </aside>
    </div>
  );
}

function PickRing({ card, pack }: { card: CaseCard; pack: Schemas["CasePack"] }) {
  const steps = (pack.workflow_steps ?? []) as Array<{ key: string }>;
  const index = steps.findIndex((s) => s.key === card.stage);
  return index >= 0 ? <StepRing step={stepLabel(card.stage!)} number={index + 1} total={steps.length} /> : null;
}

/** Today's date as YYYY-MM-DD in the person's time zone. */
function isoDay(date: Date, timeZone: string | null) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: timeZone ?? undefined, year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}

/** Seven days from today with each deadline and due item (S03a). */
function Week({ cases, needs, timezone, locale }: { cases: CaseCard[]; needs: Schemas["NeedsYouItem"][]; timezone: string | null; locale: string | null }) {
  const start = isoDay(new Date(), timezone);
  const [y, m, d] = start.split("-").map(Number);
  const days = Array.from({ length: 7 }, (_, i) => new Date(Date.UTC(y!, m! - 1, d! + i)));
  const label = (id: string) => cases.find((c) => c.case_id === id)?.label;
  const dueOn = (iso: string) => {
    const names = new Set<string>();
    for (const c of cases) if (c.due_date === iso) names.add(c.label);
    for (const n of needs) {
      const name = n.case_id ? label(n.case_id) : undefined;
      if (n.due_at && name && isoDay(new Date(n.due_at), timezone) === iso) names.add(name);
    }
    return [...names];
  };
  const fmt = (opts: Intl.DateTimeFormatOptions, day: Date) => new Intl.DateTimeFormat(locale ?? "en-GB", { ...opts, timeZone: "UTC" }).format(day);
  return (
    <section aria-labelledby="this-week" className="av-section">
      <div className="av-today__head">
        <h2 id="this-week">{t("today.week.title")}</h2>
        <p className="av-muted">{t("today.week.note")}</p>
      </div>
      <ol className="av-week">
        {days.map((day) => {
          const due = dueOn(day.toISOString().slice(0, 10));
          return (
            <li key={day.toISOString()} className="av-week__day">
              <span className="av-week__date">
                {fmt({ weekday: "short" }, day)} <strong>{fmt({ day: "numeric" }, day)}</strong>
              </span>
              {due.length === 0 ? (
                <span className="av-muted">{t("today.week.none")}</span>
              ) : (
                due.map((name) => (
                  <span key={name}>
                    <strong>{name}</strong> · {t("today.week.due")}
                  </span>
                ))
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
