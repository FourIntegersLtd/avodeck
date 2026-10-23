// Invite accept page. It uses the sign-in layout (S01) classes and Logo; the layout itself lives in the app
// layer, which a capability may not import (foundation request: move AuthLayout to foundation/ui).
// Mounted outside the signed-in guard so there is no app bar; a 401 sends the person to sign in and back here.
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { t } from "../../foundation/i18n";
import { ApiError, api, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Logo, Notice } from "../../foundation/ui";
import { w, type OrgKey } from "./words";

export function AcceptInvite() {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [joined, setJoined] = useState<Schemas["InviteAccepted"] | null>(null);

  const join = async () => {
    setBusy(true);
    setFailed(false);
    try {
      setJoined(await unwrap(api.POST("/v1/invites/{token}/accept", { params: { path: { token } } })));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        navigate("/sign-in", { replace: true, state: { from: `/invites/${token}` } });
        return;
      }
      setFailed(true);
      setBusy(false);
    }
  };

  return (
    <div className="av-sign-in">
      <aside className="av-sign-in__brand">
        <Logo onDark />
        <div className="av-sign-in__promise">
          <h2 className="av-sign-in__tagline">{t("sign_in.brand.tagline")}</h2>
        </div>
      </aside>
      <main className="av-sign-in__main" id="main">
        <div className="av-sign-in__form av-stack">
          <h1>{w("accept.title")}</h1>
          {joined ? (
            <>
              <Notice tone="success">
                <p>{w("accept.done", { role: w(`role_a.${joined.role}` as OrgKey) })}</p>
              </Notice>
              <div className="av-row">
                <Link className="av-button" to={`/organisations/${joined.organisation_id}`}>
                  {w("accept.open")}
                </Link>
                <Link className="av-button av-button--secondary" to="/today">
                  {w("accept.today")}
                </Link>
              </div>
            </>
          ) : (
            <>
              <p>{w("accept.body")}</p>
              {failed && (
                <Notice tone="error">
                  <p>{w("accept.bad")}</p>
                </Notice>
              )}
              <Button disabled={busy} onClick={() => void join()}>
                {busy ? w("accept.joining") : w("accept.button")}
              </Button>
            </>
          )}
        </div>
      </main>
    </div>
  );
}
