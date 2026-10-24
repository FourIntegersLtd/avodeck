import { t } from "../i18n";
import { Button } from "./Button";
import { Notice } from "./Notice";
import { Spinner } from "./Spinner";

/** The page loader, centred in the content area. `inline` is the compact row for a card or panel.
 * The words are there at once for screen readers; the picture fades in after 200ms (styles.css). */
export function Loading({ inline = false }: { inline?: boolean }) {
  return (
    <p role="status" className={inline ? "av-muted av-loading av-loading--inline" : "av-muted av-loading"}>
      <Spinner />
      {t("app.loading")}
    </p>
  );
}

/** Shown while fresh data loads behind what is already on screen, so the person knows their action is being done. */
export function Updating({ label }: { label: string }) {
  return (
    <p role="status" className="av-muted av-updating">
      <Spinner />
      {label}
    </p>
  );
}

export function LoadError({ onRetry, message }: { onRetry: () => void; message?: string }) {
  return (
    <Notice tone="error">
      <p>{message ?? t("errors.load")}</p>
      <Button variant="secondary" onClick={onRetry}>
        {t("errors.retry")}
      </Button>
    </Notice>
  );
}
