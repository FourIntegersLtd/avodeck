import { useEffect, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { LegalGate, type LegalStatusResponse } from "../capabilities/account/legal/LegalGate";
import { t } from "../foundation/i18n";
import { JobWatchProvider } from "../foundation/lib/jobWatch";
import { MessagesProvider } from "../foundation/lib/messages";
import { ToastProvider } from "../foundation/lib/toasts";
import { claimResourceCache } from "../foundation/lib/resourceCache";
import { SessionContext } from "../foundation/lib/session";
import { useResource } from "../foundation/lib/useResource";
import { api, onPasswordChangeRequired, unwrap } from "../foundation/platform-client";
import { AppShell, LoadError, Loading } from "../foundation/ui";
import { ChoosePassword } from "./auth/ChoosePassword";

/** Expert-only places; support staff go to the console instead. A case they were granted stays open. */
const EXPERT_ONLY = new Set(["/today", "/profile", "/cases", "/cases/new"]);

async function fetchLegalStatus(): Promise<LegalStatusResponse | null> {
  try {
    return await unwrap(api.GET("/v1/legal-agreements/status"));
  } catch {
    return null;
  }
}

/** Signed-in screens: no session goes to sign in; a temporary password from support is changed first
 * (ADR 0024; the server refuses everything else). The password alone is enough (ADR 0025): the code
 * from the phone is asked for at risky actions (foundation/lib/stepUp.tsx). */
export function RequireSession() {
  const location = useLocation();
  const session = useResource(async () => {
    const status = await unwrap(api.GET("/v1/auth/session"));
    const [me, legal] = await Promise.all([unwrap(api.GET("/v1/me")), fetchLegalStatus()]);
    return { status, me, legal };
  }, []);
  const [blocked, setBlocked] = useState(false);
  const [legalDone, setLegalDone] = useState(false);
  useEffect(() => onPasswordChangeRequired(() => setBlocked(true)), []);

  if (session.error?.status === 401) return <Navigate to="/sign-in" replace state={{ from: location.pathname }} />;
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
  if (session.data.legal && !session.data.legal.accepted && !legalDone && location.pathname !== "/agree") {
    return <LegalGate from={location.pathname} onDone={() => { setLegalDone(true); session.reload(); }} />;
  }
  claimResourceCache(session.data.me.person.id); // a different person empties the screen cache
  const isSupport = session.data.me.is_support_staff === true;
  if (isSupport && EXPERT_ONLY.has(location.pathname.replace(/\/$/, ""))) return <Navigate to="/support" replace />;
  return (
    <SessionContext.Provider value={{ me: session.data.me, status: session.data.status, isSupport }}>
      <MessagesProvider>
        <ToastProvider>
          <JobWatchProvider>
            <AppShell name={session.data.me.person.full_name || session.data.me.person.email} support={isSupport}>
              <Outlet />
            </AppShell>
          </JobWatchProvider>
        </ToastProvider>
      </MessagesProvider>
    </SessionContext.Provider>
  );
}
