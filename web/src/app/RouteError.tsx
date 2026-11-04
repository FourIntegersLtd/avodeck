import { useEffect, useState } from "react";
import { useRouteError } from "react-router-dom";
import { t } from "../foundation/i18n";
import { isMissingScreenFile, reloadOnceForNewRelease } from "../foundation/lib/staleRelease";
import { Button } from "../foundation/ui";

/** Shown in place of the router's built-in error page. A screen file missing after a new release reloads
 * the page once (the address is already the page being opened); anything else gets a plain message. */
export function RouteError() {
  const error = useRouteError();
  const [reloading, setReloading] = useState(() => isMissingScreenFile(error));

  useEffect(() => {
    if (reloading && !reloadOnceForNewRelease()) setReloading(false);
  }, [reloading]);

  if (reloading) {
    return (
      <main className="av-page av-stack" id="main">
        <p role="status">{t("app.new_release.body")}</p>
      </main>
    );
  }
  return (
    <main className="av-page av-stack" id="main">
      <h1>{t("app.route_error.title")}</h1>
      <p>{t("app.route_error.body")}</p>
      <Button onClick={() => window.location.reload()}>{t("app.route_error.action")}</Button>
    </main>
  );
}
