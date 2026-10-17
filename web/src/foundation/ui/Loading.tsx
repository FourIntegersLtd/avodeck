import { t } from "../i18n";
import { Button } from "./Button";
import { Notice } from "./Notice";

export function Loading() {
  return (
    <p role="status" className="av-muted">
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
