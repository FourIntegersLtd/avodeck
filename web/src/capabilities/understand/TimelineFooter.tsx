// S11 footer: how many entries are drawn, the key to the dots and marks, and the way on to the things we found.
// Under both the board and the list. Kinds of record show the pack's words; a raw key is made readable.
import { humanise, t } from "../../foundation/i18n";
import { screenPath } from "../../foundation/lib/screens";
import type { Schemas } from "../../foundation/platform-client";
import { LinkButton, QChip } from "../../foundation/ui";
import { DateMarkIcon } from "./laneIcons";
import { w } from "./words";

const NUMBER = new Intl.NumberFormat("en-GB");

/** The pack's word for a kind of record; when the pack has none the API sends the key back, so make it plain. */
export const sourceLabel = (s: Schemas["TimelineSource"]) => (s.label && s.label !== s.key ? s.label : humanise(s.key));

export function TimelineFooter({ caseId, shown, total, sources, found }: { caseId: string; shown: number; total: number; sources?: Schemas["TimelineSource"][]; found: number }) {
  return (
    <div className="av-calm-foot">
      <p className="av-story-shown">{w("timeline.shown", { shown: NUMBER.format(shown), total: NUMBER.format(total) })}</p>
      <p className="av-muted av-calm-foot__hint">{w("timeline.blind_read_hint")}</p>
      <ul className="av-understand-legend" aria-label={w("timeline.legend_dots")}>
        <li><span className="av-understand-key av-understand-key--checked" aria-hidden="true" />{t("ui.status.checked")}</li>
        <li><span className="av-understand-key av-understand-key--please_check" aria-hidden="true" />{t("ui.status.please_check")}</li>
        <li><span className="av-understand-key av-understand-key--look" aria-hidden="true" />{t("ui.status.look")}</li>
        <li><QChip n={2} />{w("timeline.legend_question")}</li>
        <li><DateMarkIcon />{w("timeline.check_the_date")}</li>
      </ul>
      {sources && sources.length > 0 && (
        <ul className="av-understand-legend av-understand-legend--kinds" aria-label={w("timeline.legend")}>
          {sources.map((s) => (
            <li key={s.key}>
              <span className="av-understand-swatch" style={{ background: s.colour ?? undefined }} aria-hidden="true" />
              {sourceLabel(s)}
            </li>
          ))}
        </ul>
      )}
      {found > 0 && (
        <div>
          <LinkButton variant="outline" to={screenPath("things_to_look_at", { caseId }) ?? `/cases/${caseId}/things-to-look-at`} arrow>
            {w("timeline.look", { count: found })}
          </LinkButton>
        </div>
      )}
    </div>
  );
}
