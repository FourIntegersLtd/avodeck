import { t } from "../i18n";
import { Button } from "./Button";
import { Notice } from "./Notice";
import { Spinner } from "./Spinner";

export function Loading() {
  return (
    <p role="status" className="av-muted av-loading">
      <Spinner />
      {t("app.loading")}
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
