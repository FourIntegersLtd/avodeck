import { t } from "../foundation/i18n";
import { screenPath } from "../foundation/lib/screens";
import { ProgressBar } from "../foundation/ui";

/** The case home's "Where you are": pages read so far, and a way to the ones still to read (S03 board, case frame). */
export function WhereYouAre({ caseId, read, total, locale }: { caseId: string; read: number; total: number; locale: string }) {
  if (total <= 0) return null;
  const left = Math.max(0, total - read);
  const percent = Math.round((read / total) * 100);
  const n = (x: number) => x.toLocaleString(locale);
  const to = screenPath("reader", { caseId });
  return (
    <section aria-labelledby="where-you-are" className="av-card av-where">
      <h2 id="where-you-are">{t("case.where.heading")}</h2>
      <div className="av-where__line">
        <span>
          <strong>{t("case.where.read", { read: n(read), total: n(total) })}</strong> {left > 0 && <span>{t("case.where.left", { left: n(left) })}</span>}
        </span>
        <span className="av-mono">{percent}%</span>
      </div>
      <ProgressBar value={percent} max={100} label={t("case.where.percent", { percent })} />
      {left > 0 && (
        <div className="av-where__banner">
          <p>{t("case.where.unread", { count: left, formatted: n(left) })}</p>
          {to && <a href={to}>{t("case.where.show")}</a>}
        </div>
      )}
    </section>
  );
}
