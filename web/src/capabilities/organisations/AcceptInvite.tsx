// Invite accept page, in the sign-in layout (S01).
// Mounted outside the signed-in guard so there is no app bar; a 401 sends the person to sign in and back here.
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, api, unwrap, type Schemas } from "../../foundation/platform-client";
import { AuthLayout, Button, Notice } from "../../foundation/ui";
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
    <AuthLayout>
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
    </AuthLayout>
  );
}
