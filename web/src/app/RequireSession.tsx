import { Navigate, Outlet, useLocation } from "react-router-dom";
import { t } from "../foundation/i18n";
import { MessagesProvider } from "../foundation/lib/messages";
import { SessionContext } from "../foundation/lib/session";
import { useResource } from "../foundation/lib/useResource";
import { api, unwrap } from "../foundation/platform-client";
import { AppShell, LoadError, Loading } from "../foundation/ui";

/** Signed-in screens: no session goes to sign in; a session without the code goes to the code step. */
export function RequireSession() {
  const location = useLocation();
  const session = useResource(async () => {
    const status = await unwrap(api.GET("/v1/auth/session"));
    if (!status.mfa_complete) return { status, me: null };
    return { status, me: await unwrap(api.GET("/v1/me")) };
  }, []);

  if (session.error?.status === 401) return <Navigate to="/sign-in" replace state={{ from: location.pathname }} />;
  if (session.data && !session.data.me) {
    return <Navigate to="/sign-in" replace state={{ from: location.pathname, step: session.data.status.mfa_enrolled ? "code" : "enrol" }} />;
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
  return (
    <SessionContext.Provider value={{ me: session.data.me, status: session.data.status }}>
      <MessagesProvider>
        <AppShell name={session.data.me.person.full_name || session.data.me.person.email}>
          <Outlet />
        </AppShell>
      </MessagesProvider>
    </SessionContext.Provider>
  );
}
