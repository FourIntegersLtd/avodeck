// S27 Organisation and members (docs/design/screens/S27-OrgMembers.dc.html). The server decides who may do
// what; this screen shows the controls to owners and admins and says in plain words when it is refused.
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { screenPath } from "../../foundation/lib/screens";
import { useSession } from "../../foundation/lib/session";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, unwrap, type Schemas } from "../../foundation/platform-client";
import { BackLink, Button, Dialog, LoadError, Loading, TextField, SectionIcon, TextLink, ToggleSwitch } from "../../foundation/ui";
import { MessageNotice, NotFoundPage, useAct } from "./common";
import { People } from "./People";
import { w, yearsText, type OrgKey } from "./words";

type Org = Schemas["Organisation"];

export function OrganisationSettings() {
  const { orgId = "" } = useParams();
  const { me } = useSession();
  const org = useResource(() => unwrap(api.GET("/v1/organisations/{org_id}", { params: { path: { org_id: orgId } } })), [orgId], { cache: "organisations/OrganisationSettings#OrganisationSettings:org" });
  const members = useResource(() => unwrap(api.GET("/v1/organisations/{org_id}/members", { params: { path: { org_id: orgId } } })), [orgId], { cache: "organisations/OrganisationSettings#OrganisationSettings:members" });
  const [policy, setPolicy] = useState<Org["policy"] | null>(null);

  if (org.error?.status === 404) return <NotFoundPage />;
  if (org.error || members.error) {
    return (
      <div className="av-page">
        <LoadError onRetry={() => (org.reload(), members.reload())} />
      </div>
    );
  }
  if (!org.data || !members.data) {
    return (
      <div className="av-page">
        <Loading />
      </div>
    );
  }
  const item = org.data;
  const can = (action: string) => item.allowed_actions.includes(action);
  const manage = can("org.manage_members");
  const personal = item.kind === "personal";
  return (
    <div className="av-page av-org av-stack">
      <BackLink to="/account">{w("org.back")}</BackLink>
      <div className="av-org-head">
        <div>
          <p className="av-field__label av-org-eyebrow">{w("org.eyebrow")}</p>
          <h1>{item.name}</h1>
          <p className="av-muted">{personal ? w("org.intro.personal") : w("org.intro.team", { role: w(`role_a.${item.role}` as OrgKey) })}</p>
        </div>
        <Switcher current={item.id} memberships={me.memberships} />
      </div>
      <People orgId={item.id} members={members.data.current_members} manage={manage} assignable={item.assignable_roles} me={{ personId: me.person.id, locale: me.person.locale }} reload={members.reload} />
      <div className="av-org-panels">
        <section aria-labelledby="roles-panel" className="av-card av-org-card">
          <h2 id="roles-panel">{w("panel.roles.title")}</h2>
          <p>
            <strong>{w("panel.roles.owner_admin")}</strong> {w("panel.roles.owner_admin_body")}
          </p>
          <p>
            <strong>{w("panel.roles.expert")}</strong> {w("panel.roles.expert_body")}
          </p>
          <p>
            <strong>{w("panel.roles.assistant")}</strong> {w("panel.roles.assistant_body")}
          </p>
          <p>
            <strong>{w("panel.roles.viewer")}</strong> {w("panel.roles.viewer_body")}
          </p>
        </section>
        <section aria-labelledby="leaves-panel" className="av-card av-org-card">
          <h2 id="leaves-panel">{w("panel.leaves.title")}</h2>
          <p>
            {w("panel.leaves.access")} <strong>{w("panel.leaves.except")}</strong> {w("panel.leaves.access_end")}
          </p>
          <p>{w("panel.leaves.move")}</p>
          <p>{w("panel.leaves.owner")}</p>
        </section>
      </div>
      <Rules org={{ ...item, policy: policy ?? item.policy }} manage={can("org.set_policy")} onPolicy={setPolicy} />
      {can("org.billing") && !personal && <Billing orgId={item.id} />}
      {can("org.close") && !personal && <CloseOrganisation org={item} />}
    </div>
  );
}

function Switcher({ current, memberships }: { current: string; memberships: Schemas["Membership"][] }) {
  const navigate = useNavigate();
  if (memberships.length < 2) return null;
  return (
    <div role="group" aria-label={w("org.switch")} className="av-org-switch">
      {memberships.map((m) => (
        <Button key={m.organisation_id} variant={m.organisation_id === current ? "primary" : "secondary"} aria-pressed={m.organisation_id === current} onClick={() => navigate(`/organisations/${m.organisation_id}`)}>
          {m.kind === "personal" ? w("org.mine") : m.name}
        </Button>
      ))}
    </div>
  );
}

function Rules({ org, manage, onPolicy }: { org: Org; manage: boolean; onPolicy: (p: Org["policy"]) => void }) {
  const { act, busy, message } = useAct();
  const [editing, setEditing] = useState(false);
  const [years, setYears] = useState("");
  const [invalid, setInvalid] = useState(false);
  const mine = useResource(async () => {
    try {
      return await unwrap(api.GET("/v1/me/template"));
    } catch (err) {
      if (err instanceof ApiError) return null;
      throw err;
    }
  }, [org.id]);
  const { policy } = org;

  if (org.kind === "personal") {
    return (
      <section aria-labelledby="rules-title" className="av-card av-org-card">
        <SectionIcon glyph="shield" />
        <h2 id="rules-title">{w("rules.title", { name: org.name })}</h2>
        <p className="av-muted">{w("rules.not_for_personal")}</p>
      </section>
    );
  }
  const save = async (next: Org["policy"], done?: () => void) => {
    const saved = await act(() => unwrap(api.PUT("/v1/organisations/{org_id}/policy", { params: { path: { org_id: org.id } }, body: next })));
    if (saved) {
      onPolicy(saved.policy);
      done?.();
    }
  };
  const toggleTemplate = (on: boolean) => {
    if (on && !mine.data) return;
    void save({ required_template_id: on ? (mine.data?.id ?? null) : null, max_retention_years: policy.max_retention_years ?? null });
  };
  const saveYears = () => {
    const text = years.trim();
    const n = Number(text);
    if (text !== "" && (!Number.isInteger(n) || n < 1 || n > 100)) return setInvalid(true);
    setInvalid(false);
    return save({ required_template_id: policy.required_template_id ?? null, max_retention_years: text === "" ? null : n }, () => setEditing(false));
  };
  const on = Boolean(policy.required_template_id);
  return (
    <section aria-labelledby="rules-title" className="av-card av-org-card">
      <SectionIcon glyph="shield" />
      <h2 id="rules-title">{w("rules.title", { name: org.name })}</h2>
      <MessageNotice message={message} />
      <div className="av-row-item">
        <div className="av-row-item__text">
          <p className="av-field__label">{w("rules.template")}</p>
          <p className="av-muted">{on ? w("rules.template_on") : mine.data || !manage ? w("rules.template_off") : w("rules.template_none")}</p>
        </div>
        {manage && <ToggleSwitch label={w("rules.template_switch")} checked={on} disabled={busy || (!on && !mine.data)} onChange={toggleTemplate} />}
      </div>
      <div className="av-row-item">
        <div className="av-row-item__text">
          <p className="av-field__label">{w("rules.retention")}</p>
          <p className="av-muted">{w("rules.retention_hint")}</p>
        </div>
        {editing ? (
          <div className="av-row">
            <TextField label={w("rules.retention_label")} name="years" type="number" inputMode="numeric" min={1} max={100} value={years} onChange={(e) => setYears(e.target.value)} hint={w("rules.retention_clear")} error={invalid ? w("rules.retention_invalid") : undefined} />
            <Button onClick={saveYears}>{w("rules.retention_save")}</Button>
            <Button variant="quiet" onClick={() => setEditing(false)}>
              {w("rules.retention_cancel")}
            </Button>
          </div>
        ) : (
          <>
            <span className="av-org-years">{policy.max_retention_years ? yearsText(policy.max_retention_years) : w("rules.retention_none")}</span>
            {manage && (
              <Button
                variant="quiet"
                onClick={() => {
                  setYears(policy.max_retention_years ? String(policy.max_retention_years) : "");
                  setEditing(true);
                }}
              >
                {w("rules.retention_change")}
              </Button>
            )}
          </>
        )}
      </div>
    </section>
  );
}

function Billing({ orgId }: { orgId: string }) {
  const found = useResource(() => unwrap(api.GET("/v1/organisations/{org_id}/billing-profile", { params: { path: { org_id: orgId } } })), [orgId], { cache: "organisations/OrganisationSettings#Billing:found" });
  const profile = found.data?.profile;
  const bank = screenPath("billing_profile", { params: { organisation_id: orgId } });
  return (
    <section aria-labelledby="billing-title" className="av-card av-org-card">
      <SectionIcon glyph="invoice" />
      <h2 id="billing-title">{w("billing.title")}</h2>
      {found.error && <LoadError onRetry={found.reload} />}
      {!found.data && !found.error && <Loading inline />}
      {found.data && (
        <div className="av-row-item">
          <p className="av-row-item__text">{profile ? (profile.invoice_prefix ? w("billing.line", { name: profile.legal_name, prefix: profile.invoice_prefix }) : profile.legal_name) : w("billing.none")}</p>
          {bank ? (
            <TextLink to={bank}>{w("billing.bank")}</TextLink>
          ) : (
            <p className="av-muted av-small">{w("billing.bank_hint")}</p>
          )}
        </div>
      )}
    </section>
  );
}

function CloseOrganisation({ org }: { org: Org }) {
  const navigate = useNavigate();
  const { act, message, setMessage } = useAct({ conflict: "organisations.close_org.cases_open" });
  const [asking, setAsking] = useState(false);
  const close = async () => {
    const done = await act(async () => {
      await unwrap(api.POST("/v1/organisations/{org_id}/close", { params: { path: { org_id: org.id } } }));
      return true;
    });
    setAsking(false);
    if (done) navigate("/account");
  };
  return (
    <section aria-labelledby="close-org" className="av-card av-org-card">
      <MessageNotice message={message} />
      <div className="av-row-item av-row-item--head">
        <div className="av-row-item__text">
          <h2 id="close-org">{w("close_org.title", { name: org.name })}</h2>
          <p className="av-muted">{w("close_org.body")}</p>
        </div>
        <Button
          variant="danger"
          onClick={() => {
            setMessage(null);
            setAsking(true);
          }}
        >
          {w("close_org.button")}
        </Button>
      </div>
      {asking && (
        <Dialog title={w("close_org.dialog", { name: org.name })} onClose={() => setAsking(false)}>
          <p>{w("close_org.dialog_body")}</p>
          <div className="av-row">
            <Button variant="danger" onClick={() => close()}>
              {w("close_org.confirm")}
            </Button>
            <Button variant="secondary" onClick={() => setAsking(false)}>
              {w("close_org.keep")}
            </Button>
          </div>
        </Dialog>
      )}
    </section>
  );
}
