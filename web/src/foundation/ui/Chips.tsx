import { SafeLink as Link } from "./SafeLink";
import { t } from "../i18n";

/** The question a thing answers, as a small chip: "Q3". */
export function QChip({ n }: { n: number }) {
  return <span className="av-qchip">{t("ui.qchip", { n })}</span>;
}

/** A page of the bundle, in the mono face: "p.118". With `to`, it opens that page. */
export function PageRef({ page, to }: { page: number; to?: string }) {
  const words = t("ui.page_ref", { page });
  return to ? (
    <Link className="av-mono av-page-ref" to={to}>
      {words}
    </Link>
  ) : (
    <span className="av-mono av-page-ref">{words}</span>
  );
}
