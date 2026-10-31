// Inside a case: who can work on it. Owners and admins change the named expert before serving; the named
// expert lets Avodeck support open this one case for up to 24 hours. The server decides every action; the
// controls follow `allowed_actions`.
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { formatRelative } from "../../foundation/i18n";
import { useCaseFrame } from "../../foundation/lib/caseFrame";
import { errorMessage } from "../../foundation/lib/errors";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, LoadError, Loading, SectionIcon } from "../../foundation/ui";
import { MessageNotice, OWN_ERRORS, useAct, type Message } from "./common";
import { flag } from "./flags";
import { w, type OrgKey } from "./words";

const REASONS = ["help", "fault", "records"] as const;

// The replacement route is not in the generated client yet, so its path is typed as a sibling route's.
const REPLACEMENT = "/v1/cases/{case_id}/replacement" as "/v1/cases/{case_id}/named-expert";

export function CaseTeam() {
  const { item } = useCaseFrame();
  const can = new Set(item.allowed_actions);
  return (
    <div className="av-page av-page--narrow av-stack">
      <h2 className="av-org-sub">{w("case_team.title")}</h2>
      {can.has("case.reassign_named_expert") && <NamedExpert caseId={item.id} />}
      {can.has("support_access.grant") && <SupportCard caseId={item.id} />}
    </div>
  );
}

function NamedExpert({ caseId }: { caseId: string }) {
  const stepUp = useStepUp();
  const navigate = useNavigate();
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/named-expert/candidates", { params: { path: { case_id: caseId } } })), [caseId], { cache: "organisations/CaseTeam#NamedExpert:found" });
  const [open, setOpen] = useState(false);
  const [chosen, setChosen] = useState("");
  const [servedNow, setServedNow] = useState(false);
  const [message, setMessage] = useState<Message>(null);
  const [moved, setMoved] = useState(false);
  const served = servedNow || (found.data?.served ?? false);
  const candidates = found.data?.candidates ?? [];
  const nameOf = (m: Schemas["NamedExpertCandidate"]) => m.full_name || m.email;

  const move = async () => {
    setMessage(null);
    try {
      const done = await stepUp(() => unwrap(api.POST("/v1/cases/{case_id}/named-expert", { params: { path: { case_id: caseId } }, body: { person_id: chosen } })));
      if (done) {
        setMoved(true);
        setOpen(false);
        setMessage({ tone: "success", text: w("case_team.moved", { name: nameOf(candidates.find((m) => m.person_id === chosen)!) }) });
      }
    } catch (err) {
      if (err instanceof ApiError && err.code === "served_use_replacement") setServedNow(true);
      else setMessage({ tone: "error", text: errorMessage(err, OWN_ERRORS) });
    }
  };
  const replace = async () => {
    setMessage(null);
    try {
      const created = await stepUp(() => unwrap(api.POST(REPLACEMENT, { params: { path: { case_id: caseId } }, body: { person_id: chosen || (candidates[0]?.person_id ?? "") } })));
      const next = (created as { case_id?: string } | undefined)?.case_id;
      if (next) navigate(`/cases/${next}`);
    } catch (err) {
      setMessage({ tone: "error", text: errorMessage(err, OWN_ERRORS) });
    }
  };

  return (
    <section aria-labelledby="named-expert" className="av-card av-org-card">
      <SectionIcon glyph="person" />
      <h3 id="named-expert">{w("case_team.expert")}</h3>
      <p className="av-muted">{w("case_team.expert_hint")}</p>
      <MessageNotice message={message} />
      {served ? (
        <>
          <p>{w("case_team.served")}</p>
          {flag("replacement_case") && (
            <>
              <p className="av-muted">{w("case_team.replacement_hint")}</p>
              <Button onClick={() => replace()}>{w("case_team.replacement")}</Button>
            </>
          )}
        </>
      ) : open ? (
        <div className="av-stack">
          {found.error && <LoadError message={w("case_team.people_error")} onRetry={found.reload} />}
          {!found.data && !found.error && <Loading inline />}
          <div className="av-field">
            <label className="av-field__label" htmlFor="new-expert">
              {w("case_team.choose")}
            </label>
            <select id="new-expert" className="av-input av-org-select" value={chosen} onChange={(e) => setChosen(e.target.value)}>
              <option value="">{w("case_team.choose_none")}</option>
              {candidates.map((m) => (
                <option key={m.person_id} value={m.person_id}>
                  {nameOf(m)}
                </option>
              ))}
            </select>
          </div>
          <p className="av-muted">{w("case_team.warning")}</p>
          <div className="av-row">
            <Button variant="secondary" onClick={() => setOpen(false)}>
              {w("case_team.cancel")}
            </Button>
            <Button disabled={!chosen} onClick={() => move()}>
              {w("case_team.save")}
            </Button>
          </div>
        </div>
      ) : (
        !moved && (
          <Button variant="secondary" onClick={() => setOpen(true)}>
            {w("case_team.change")}
          </Button>
        )
      )}
    </section>
  );
}

function leftText(expires: string): string {
  const minutes = Math.max(0, Math.floor((new Date(expires).getTime() - Date.now()) / 60_000));
  return minutes >= 60 ? w("support.card.left_hours", { hours: Math.floor(minutes / 60), minutes: minutes % 60 }) : w("support.card.left_minutes", { minutes });
}

function SupportCard({ caseId }: { caseId: string }) {
  const { act, message } = useAct();
  const grants = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/support-access", { params: { path: { case_id: caseId } } })), [caseId], { cache: "organisations/CaseTeam#SupportCard:grants" });
  const [reason, setReason] = useState<(typeof REASONS)[number]>("help");
  const active = grants.data?.active ?? undefined;
  const ended = grants.data?.ended ?? [];
  const grant = async () => {
    const done = await act(() => unwrap(api.POST("/v1/cases/{case_id}/support-access", { params: { path: { case_id: caseId } }, body: { reason } })), () => w("support.card.granted"));
    if (done) grants.reload();
  };
  const revoke = async (id: string) => {
    const done = await act(
      async () => {
        await unwrap(api.DELETE("/v1/cases/{case_id}/support-access/{grant_id}", { params: { path: { case_id: caseId, grant_id: id } } }));
        return true;
      },
      () => w("support.card.revoked"),
    );
    if (done) grants.reload();
  };
  return (
    <section aria-labelledby="support-title" className="av-card av-org-card">
      <h3 id="support-title">{w("support.card.title")}</h3>
      <p className="av-muted">{w("support.card.intro")}</p>
      <MessageNotice message={message} />
      {grants.error && <LoadError onRetry={grants.reload} />}
      {!grants.data && !grants.error && <Loading inline />}
      {grants.data &&
        (active ? (
          <>
            <p>{w("support.card.active", { left: leftText(active.expires_at) })}</p>
            <Button variant="danger" onClick={() => revoke(active.id)}>
              {w("support.card.revoke")}
            </Button>
          </>
        ) : (
          <div className="av-stack">
            <p className="av-muted">{w("support.card.none")}</p>
            <div className="av-field">
              <label className="av-field__label" htmlFor="support-reason">
                {w("support.card.reason")}
              </label>
              <select id="support-reason" className="av-input av-org-select" value={reason} onChange={(e) => setReason(e.target.value as (typeof REASONS)[number])}>
                {REASONS.map((r) => (
                  <option key={r} value={r}>
                    {w(`support.card.reason.${r}` as OrgKey)}
                  </option>
                ))}
              </select>
            </div>
            <Button onClick={() => grant()}>{w("support.card.grant")}</Button>
          </div>
        ))}
      {ended.length > 0 && (
        <details>
          <summary>{w("support.card.earlier")}</summary>
          <ul className="av-rows" role="list">
            {ended.map((g) => (
              <li key={g.id} className="av-row-item">
                <span>{g.revoked_at ? w("support.card.ended", { date: formatRelative(new Date(g.revoked_at), null) }) : w("support.card.expired", { date: formatRelative(new Date(g.expires_at), null) })}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
