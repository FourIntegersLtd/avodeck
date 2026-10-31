// The "N things need you" list (S03): the NeedsYouItem rows slices produce.
import { hasMessage, t } from "../../foundation/i18n";
import { needsYouIcon } from "../../foundation/lib/needsYouIcons";
import { screenPath } from "../../foundation/lib/screens";
import type { Schemas } from "../../foundation/platform-client";
import { IconTile, LinkButton } from "../../foundation/ui";
import { Icon, type CaseCard } from "./shared";

export function NeedsYou({ items, cases }: { items: Schemas["NeedsYouItem"][]; cases: CaseCard[] }) {
  const ranked = [...items].sort((a, b) => a.rank - b.rank);
  const label = (id: string) => cases.find((c) => c.case_id === id)?.label ?? "";
  return (
    <section aria-labelledby="needs-you" className="av-section av-card av-needs-card">
      <h2 id="needs-you">{ranked.length ? t("today.needs_you.heading", { count: ranked.length }) : t("today.needs_you.none")}</h2>
      {ranked.length > 0 && (
        <ol className="av-needs">
          {ranked.map((item, index) => {
            const caseLabel = item.case_id ? label(item.case_id) : "";
            const params = { ...(item.params as Record<string, string | number>), case: caseLabel, count: item.count ?? undefined, minutes: item.minutes ?? undefined };
            const known = hasMessage(item.label_key);
            const detailKey = item.label_key.replace(/\.label$/, ".detail");
            const path = screenPath(item.screen, { caseId: item.case_id, params: item.params as Record<string, unknown> });
            const target = path && hasMessage(item.action_key) ? { to: path, text: t(item.action_key, params) } : item.case_id ? { to: `/cases/${item.case_id}`, text: t("today.needs_you.open") } : null;
            const words = known ? t(item.label_key, params) : t("today.needs_you.fallback", { case: caseLabel });
            // The board names the case first ("Mrs A · 127 pages still to read") unless the words already
            // do, or the row opens something outside the case (your profile).
            const namesCase = !known || t(item.label_key, { ...params, case: "\u0001" }).includes("\u0001");
            const aboutCase = !path || path.startsWith(`/cases/${item.case_id}`);
            const shape = t("today.needs_you.title", { case: "\u0001", words: "\u0002" });
            const before = shape.slice(shape.indexOf("\u0001") + 1, shape.indexOf("\u0002"));
            const after = shape.slice(shape.indexOf("\u0002") + 1);
            const title =
              caseLabel && aboutCase && !namesCase ? (
                <>
                  <span>{caseLabel}</span>
                  {before}
                  <span>{words}</span>
                  {after}
                </>
              ) : (
                words
              );
            const icon = needsYouIcon(item.kind, Boolean(item.due_at));
            return (
              <li key={`${item.kind}-${item.case_id ?? ""}-${item.rank}`} className="av-needs__item av-hairline-row">
                <IconTile tone={icon.tone}>
                  <Icon glyph={icon.glyph} size={26} />
                </IconTile>
                <div className="av-needs__text">
                  <p className="av-needs__title">{title}</p>
                  {known && detailKey !== item.label_key && hasMessage(detailKey) && <p className="av-muted">{t(detailKey, params)}</p>}
                </div>
                {target && (
                  <LinkButton to={target.to} variant={index === 0 ? "primary" : "secondary"}>
                    {target.text}
                  </LinkButton>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

