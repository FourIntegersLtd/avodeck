import { useEffect, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { t } from "../foundation/i18n";
import { MessagesProvider } from "../foundation/lib/messages";
import { claimResourceCache } from "../foundation/lib/resourceCache";
import { SessionContext } from "../foundation/lib/session";
import { useResource } from "../foundation/lib/useResource";
import { api, onPasswordChangeRequired, unwrap } from "../foundation/platform-client";
import { AppShell, LoadError, Loading } from "../foundation/ui";
import { ChoosePassword } from "./auth/ChoosePassword";

/** Expert-only places; support staff go to the console instead. A case they were granted stays open. */
const EXPERT_ONLY = new Set(["/today", "/profile", "/cases", "/cases/new"]);

/** Signed-in screens: no session goes to sign in; a session without the code goes to the code step; a
 * temporary password from support is changed first (ADR 0024; the server refuses everything else). */
export function RequireSession() {
  const location = useLocation();
  const session = useResource(async () => {
    const status = await unwrap(api.GET("/v1/auth/session"));
    if (!status.mfa_complete) return { status, me: null };
    return { status, me: await unwrap(api.GET("/v1/me")) };
  }, []);
  const [blocked, setBlocked] = useState(false);
  useEffect(() => onPasswordChangeRequired(() => setBlocked(true)), []);

  if (session.error?.status === 401) return <Navigate to="/sign-in" replace state={{ from: location.pathname }} />;
  if (session.data && !session.data.me) {
    return <Navigate to="/sign-in" replace state={{ from: location.pathname, step: session.data.status.mfa_enrolled ? "code" : "enrol" }} />;
  }
  if (blocked || (session.data?.me && session.data.status.must_change_password)) {
    return (
      <ChoosePassword
        onDone={() => {
          setBlocked(false);
          session.reload();
        }}
      />
    );
  }
  if (session.error) {
    return (
      <AppShell>
        <div className="av-page">
          <LoadError onRetry={session.reload} />
        </div>
      </AppShell>
    );
  }
  if (!session.data?.me) {
    return (
      <main className="av-page">
        <h1 className="av-visually-hidden">{t("app.name")}</h1>
        <Loading />
      </main>
    );
  }
  claimResourceCache(session.data.me.person.id); // a different person empties the screen cache
  const isSupport = session.data.me.is_support_staff === true;
  if (isSupport && EXPERT_ONLY.has(location.pathname.replace(/\/$/, ""))) return <Navigate to="/support" replace />;
  return (
    <SessionContext.Provider value={{ me: session.data.me, status: session.data.status, isSupport }}>
      <MessagesProvider>
        <AppShell name={session.data.me.person.full_name || session.data.me.person.email} support={isSupport}>
          <Outlet />
        </AppShell>
      </MessagesProvider>
    </SessionContext.Provider>
  );
}
