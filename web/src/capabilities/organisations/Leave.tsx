// Leave an organisation: a confirm screen that names the cases the person keeps as named expert.
import { useNavigate, useParams } from "react-router-dom";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap } from "../../foundation/platform-client";
import { BackLink, Button, LoadError, Loading } from "../../foundation/ui";
import { MessageNotice, NotFoundPage, useAct } from "./common";
import { w } from "./words";

export function Leave() {
  const { orgId = "" } = useParams();
  const navigate = useNavigate();
  const { act, message } = useAct({ conflict: "organisations.error.last_owner" });
  const org = useResource(() => unwrap(api.GET("/v1/organisations/{org_id}", { params: { path: { org_id: orgId } } })), [orgId]);
  const today = useResource(() => unwrap(api.GET("/v1/today")), []);

  if (org.error?.status === 404) return <NotFoundPage />;
  if (org.error || today.error) {
    return (
      <div className="av-page">
        <LoadError onRetry={() => (org.reload(), today.reload())} />
      </div>
    );
  }
  if (!org.data || !today.data) {
    return (
      <div className="av-page">
        <Loading />
      </div>
    );
  }
  const { name, kind } = org.data;
  const kept = today.data.cases.filter((c) => c.organisation_id === orgId && c.your_role === "named_expert");
  const leave = async () => {
    const done = await act(async () => {
      await unwrap(api.POST("/v1/organisations/{org_id}/leave", { params: { path: { org_id: orgId } } }));
      return true;
    });
    if (done) navigate("/account");
  };
  return (
    <div className="av-page av-page--narrow av-stack">
      <BackLink to="/account">{w("org.back")}</BackLink>
      <h1>{w("leave.title", { name })}</h1>
      <p>{w("leave.intro")}</p>
      <MessageNotice message={message} />
      {kind === "personal" ? (
        <p>{w("leave.personal")}</p>
      ) : (
        <>
          <section aria-labelledby="keep-title" className="av-card av-org-card">
            <h2 id="keep-title">{w("leave.keep_title")}</h2>
            {kept.length === 0 ? (
              <p className="av-muted">{w("leave.keep_none")}</p>
            ) : (
              <>
                <p className="av-muted">{w("leave.keep_body")}</p>
                <ul className="av-rows" role="list">
                  {kept.map((c) => (
                    <li key={c.case_id} className="av-row-item">
                      <span className="av-field__label">{c.label}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>
          <p className="av-muted">{w("leave.owner_note")}</p>
          <div className="av-row av-org-actions">
            <Button variant="secondary" onClick={() => navigate("/account")}>
              {w("leave.stay")}
            </Button>
            <Button variant="danger" onClick={() => void leave()}>
              {w("leave.button", { name })}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
