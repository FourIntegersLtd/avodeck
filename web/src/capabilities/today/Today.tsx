// S03 Today: what needs the expert now (NeedsYouItem rows slices produce) and their open cases.
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { formatDay, formatDue, hasMessage, hourIn, t } from "../../foundation/i18n";
import { partyLine } from "../../foundation/lib/pack";
import { hasScreen, screenPath } from "../../foundation/lib/screens";
import { useSession } from "../../foundation/lib/session";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { LinkButton, LoadError, Loading, stepLabel } from "../../foundation/ui";

type CasePack = Schemas["CasePack"];
type CaseCard = Schemas["CaseCard"];

function greetingKey(hour: number) {
  return hour < 12 ? "today.greeting.morning" : hour < 18 ? "today.greeting.afternoon" : "today.greeting.evening";
}

const combination = (c: CaseCard) => `${c.jurisdiction}/${c.case_type}/${c.specialty}`;

/** One pack per combination of jurisdiction, case type and specialty among the cards. */
function usePacks(cases: CaseCard[] | undefined) {
  const firstOfEach = useMemo(() => {
    const seen = new Map<string, string>();
    for (const c of cases ?? []) if (!seen.has(combination(c))) seen.set(combination(c), c.case_id);
    return [...seen.entries()];
  }, [cases]);
  return useResource(async () => {
    const loaded = await Promise.all(
      firstOfEach.map(async ([combo, caseId]) => [combo, await unwrap(api.GET("/v1/cases/{case_id}/pack", { params: { path: { case_id: caseId } } }))] as const),
    );
    return new Map<string, CasePack>(loaded);
  }, [firstOfEach]);
}

export function Today() {
  const { me } = useSession();
  const today = useResource(() => unwrap(api.GET("/v1/today")), []);
  const packs = usePacks(today.data?.cases);
  const [org, setOrg] = useState<string | null>(null);
  const name = me.person.full_name || me.person.email;
  const locale = me.person.locale;

  return (
    <div className="av-page">
      <div className="av-today__head">
        <div>
          <h1>{t(greetingKey(hourIn(me.person.timezone)), { name })}</h1>
          <p className="av-muted">{formatDay(new Date(), locale ?? undefined, me.person.timezone)}</p>
        </div>
        {hasScreen("new_case") && <LinkButton to={screenPath("new_case")!}>{t("today.cases.new")}</LinkButton>}
      </div>
      {today.error && <LoadError onRetry={today.reload} />}
      {!today.data && !today.error && <Loading />}
      {today.data && (
        <>
          <NeedsYou items={today.data.needs_you} cases={today.data.cases} />
          <Cases data={today.data} packs={packs.data} locale={locale} org={org} setOrg={setOrg} />
        </>
      )}
    </div>
  );
}

function NeedsYou({ items, cases }: { items: Schemas["NeedsYouItem"][]; cases: CaseCard[] }) {
  const ranked = [...items].sort((a, b) => a.rank - b.rank);
  const label = (id: string) => cases.find((c) => c.case_id === id)?.label ?? "";
  return (
    <section aria-labelledby="needs-you" className="av-section">
      <h2 id="needs-you">{ranked.length ? t("today.needs_you.heading", { count: ranked.length }) : t("today.needs_you.none")}</h2>
      {ranked.length > 0 && (
        <ol className="av-needs">
          {ranked.map((item) => {
            const caseLabel = item.case_id ? label(item.case_id) : "";
            const params = { ...(item.params as Record<string, string | number>), case: caseLabel, count: item.count ?? undefined, minutes: item.minutes ?? undefined };
            const known = hasMessage(item.label_key);
            const detailKey = item.label_key.replace(/\.label$/, ".detail");
            const path = screenPath(item.screen, { caseId: item.case_id, params: item.params as Record<string, unknown> });
            const target = path && hasMessage(item.action_key) ? { to: path, text: t(item.action_key, params) } : item.case_id ? { to: `/cases/${item.case_id}`, text: t("today.needs_you.open") } : null;
            return (
              <li key={`${item.kind}-${item.case_id ?? ""}-${item.rank}`} className="av-card av-needs__item">
                <div>
                  <p className="av-case-card__title">{known ? t(item.label_key, params) : t("today.needs_you.fallback", { case: caseLabel })}</p>
                  {known && detailKey !== item.label_key && hasMessage(detailKey) && <p className="av-muted">{t(detailKey, params)}</p>}
                </div>
                {target && <LinkButton to={target.to}>{target.text}</LinkButton>}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

interface CasesProps {
  data: Schemas["Today"];
  packs: Map<string, CasePack> | undefined;
  locale: string | null | undefined;
  org: string | null;
  setOrg: (org: string | null) => void;
}

function Cases({ data, packs, locale, org, setOrg }: CasesProps) {
  const shown = org ? data.cases.filter((c) => c.organisation_id === org) : data.cases;
  const orgName = (o: Schemas["TodayOrganisation"]) => (o.kind === "personal" ? t("today.filter.personal") : o.name);
  return (
    <section aria-labelledby="your-cases" className="av-section">
      <div className="av-today__head">
        <h2 id="your-cases">{t("today.cases.title")}</h2>
        <p className="av-muted">{t("today.cases.counts", { open: shown.length, closed: data.closed_count })}</p>
      </div>
      {data.organisations.length > 1 && (
        <div className="av-filter" role="group" aria-label={t("today.filter.label")}>
          <button type="button" className="av-button av-button--secondary" aria-pressed={org === null} onClick={() => setOrg(null)}>
            {t("today.filter.option", { name: t("today.filter.all"), count: data.cases.length })}
          </button>
          {data.organisations.map((o) => (
            <button key={o.id} type="button" className="av-button av-button--secondary" aria-pressed={org === o.id} onClick={() => setOrg(o.id)}>
              {t("today.filter.option", { name: orgName(o), count: data.cases.filter((c) => c.organisation_id === o.id).length })}
            </button>
          ))}
        </div>
      )}
      {shown.length === 0 ? (
        <p>{t("today.cases.none")}</p>
      ) : (
        <ul className="av-cases">
          {shown.map((c) => (
            <li key={c.case_id}>
              <CaseCardView card={c} pack={packs?.get(combination(c))} locale={locale} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function CaseCardView({ card, pack, locale }: { card: CaseCard; pack: CasePack | undefined; locale: string | null | undefined }) {
  const steps = (pack?.workflow_steps ?? []) as Array<{ key: string }>;
  const stepIndex = steps.findIndex((s) => s.key === card.stage);
  const party = partyLine(pack, card.party_role);
  const dateLocale = locale ?? pack?.locale;
  return (
    <Link to={`/cases/${card.case_id}`} className="av-card av-case-card">
      <h3 className="av-case-card__title">{card.label}</h3>
      {card.organisation_kind !== "personal" && <span className="av-badge">{card.organisation_name}</span>}
      {party && <span>{party}</span>}
      {stepIndex >= 0 && (
        <span className="av-row">
          <span className="av-badge">{stepLabel(card.stage!)}</span>
          <span className="av-muted">{t("case.step", { number: stepIndex + 1, total: steps.length })}</span>
        </span>
      )}
      {card.due_date && pack && <span>{t("case.due", { date: formatDue(card.due_date, dateLocale) })}</span>}
      {card.on_hold && <span className="av-badge av-badge--look">{t("case.on_hold")}</span>}
    </Link>
  );
}
