// S27: the people table, pending invites and the invite form.
import { useState, type FormEvent } from "react";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { useStepUp } from "../../foundation/lib/stepUp";
import { Button, Dialog, TextField, initials } from "../../foundation/ui";
import { MessageNotice, shortDate, useAct } from "./common";
import { casesText, w, type OrgKey } from "./words";

type Member = Schemas["OrgMember"];
type Invite = Schemas["Invite"];
type Role = Member["role"];

const INVITE_ROLES: Role[] = ["expert", "assistant", "admin", "viewer"];
const DAY = 86_400_000;

const roleName = (role: string) => w(`role.${role}` as OrgKey);

interface Props {
  orgId: string;
  members: Member[];
  manage: boolean;
  /** The roles the server says this person may give. */
  assignable: Role[];
  me: { personId: string; locale: string | null | undefined };
  reload: () => void;
}

export function People({ orgId, members, manage, assignable, me, reload }: Props) {
  const { act, message } = useAct();
  const [editing, setEditing] = useState<string | null>(null);
  const [newRole, setNewRole] = useState<Role>(assignable[0] ?? "expert");
  const [removing, setRemoving] = useState<Member | null>(null);
  const current = members.filter((m) => !m.ended_at);
  const nameOf = (m: Member) => m.full_name || m.email;

  const saveRole = async (m: Member) => {
    const done = await act(
      () => unwrap(api.PATCH("/v1/organisations/{org_id}/members/{membership_id}", { params: { path: { org_id: orgId, membership_id: m.membership_id } }, body: { role: newRole } })),
      () => w("people.role_saved", { name: nameOf(m), role: roleName(newRole) }),
    );
    if (done) {
      setEditing(null);
      reload();
    }
  };
  const remove = async (m: Member) => {
    const done = await act(
      async () => {
        await unwrap(api.DELETE("/v1/organisations/{org_id}/members/{membership_id}", { params: { path: { org_id: orgId, membership_id: m.membership_id } } }));
        return true;
      },
      () => w("people.removed", { name: nameOf(m) }),
    );
    setRemoving(null);
    if (done) reload();
  };

  return (
    <section aria-labelledby="people-title" className="av-card av-org-card">
      <div className="av-row-item av-row-item--head">
        <h2 id="people-title">{w("people.title", { count: current.length })}</h2>
        <span className="av-muted">{w("people.columns")}</span>
      </div>
      <MessageNotice message={message} />
      <ul className="av-rows" role="list">
        {current.map((m) => {
          const self = m.person_id === me.personId;
          return (
            <li key={m.membership_id} className="av-row-item av-org-person">
              <span className="av-org-avatar" aria-hidden="true">
                {initials(m.full_name || m.email)}
              </span>
              <div className="av-row-item__text">
                <p className="av-field__label">{self ? w("people.you", { name: nameOf(m) }) : nameOf(m)}</p>
                {m.full_name && <p className="av-muted">{m.email}</p>}
              </div>
              {editing === m.membership_id ? (
                <div className="av-row">
                  <label className="av-field__label" htmlFor={`role-${m.membership_id}`}>
                    {w("people.new_role", { name: nameOf(m) })}
                  </label>
                  <select id={`role-${m.membership_id}`} className="av-input av-org-select" value={newRole} onChange={(e) => setNewRole(e.target.value as Role)}>
                    {assignable.map((r) => (
                      <option key={r} value={r}>
                        {roleName(r)}
                      </option>
                    ))}
                  </select>
                  <Button onClick={() => void saveRole(m)}>{w("people.save")}</Button>
                  <Button variant="quiet" onClick={() => setEditing(null)}>
                    {w("people.cancel")}
                  </Button>
                </div>
              ) : (
                <>
                  <div className="av-org-role">
                    <p className="av-field__label">{roleName(m.role)}</p>
                    <p className="av-muted">{w(`role_does.${m.role}` as OrgKey)}</p>
                  </div>
                  {m.case_count != null && <span className="av-org-cases">{casesText(m.case_count)}</span>}
                  <span className="av-muted av-org-joined">{w("people.joined", { year: new Date(m.started_at).getFullYear() })}</span>
                  {manage && (
                    <>
                      <Button
                        variant="quiet"
                        aria-label={w("people.change_for", { name: nameOf(m) })}
                        onClick={() => {
                          setEditing(m.membership_id);
                          setNewRole(assignable.includes(m.role) ? m.role : (assignable[0] ?? m.role));
                        }}
                      >
                        {w("people.change")}
                      </Button>
                      {!self && (
                        <Button variant="quiet" aria-label={w("people.remove_for", { name: nameOf(m) })} onClick={() => setRemoving(m)}>
                          {w("people.remove")}
                        </Button>
                      )}
                    </>
                  )}
                </>
              )}
            </li>
          );
        })}
      </ul>
      {manage && <Invites orgId={orgId} locale={me.locale} roles={INVITE_ROLES.filter((r) => assignable.includes(r))} />}
      {removing && (
        <Dialog title={w("people.remove_title", { name: nameOf(removing) })} onClose={() => setRemoving(null)}>
          <p>{w("people.remove_body")}</p>
          <div className="av-row">
            <Button variant="danger" onClick={() => void remove(removing)}>
              {w("people.remove_confirm")}
            </Button>
            <Button variant="secondary" onClick={() => setRemoving(null)}>
              {w("people.cancel")}
            </Button>
          </div>
        </Dialog>
      )}
    </section>
  );
}

function Invites({ orgId, locale, roles }: { orgId: string; locale: string | null | undefined; roles: Role[] }) {
  const stepUp = useStepUp();
  const { act, message } = useAct();
  const [invites, setInvites] = useState<Invite[] | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>(roles[0] ?? "expert");
  const [needEmail, setNeedEmail] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const found = await stepUp(() => unwrap(api.GET("/v1/organisations/{org_id}/invites", { params: { path: { org_id: orgId } } })));
      setInvites(found ? found.invites.filter((i) => i.status === "pending") : null);
    } catch {
      setInvites(null);
    }
  };
  const send = async (e: FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return setNeedEmail(true);
    setNeedEmail(false);
    setBusy(true);
    const sent = await act(
      () => unwrap(api.POST("/v1/organisations/{org_id}/invites", { params: { path: { org_id: orgId } }, body: { email: email.trim(), role } })),
      (invite) => w("invites.sent", { email: invite.email }),
    );
    setBusy(false);
    if (sent) {
      setEmail("");
      setInvites((list) => (list === null ? null : [...list, sent]));
    }
  };
  const cancel = async (invite: Invite) => {
    const done = await act(
      async () => {
        await unwrap(api.DELETE("/v1/organisations/{org_id}/invites/{invite_id}", { params: { path: { org_id: orgId, invite_id: invite.id } } }));
        return true;
      },
      () => w("invites.cancelled", { email: invite.email }),
    );
    if (done) setInvites((list) => (list ?? []).filter((i) => i.id !== invite.id));
  };

  return (
    <>
      {invites === null && (
        <div className="av-row-item">
          <p className="av-muted av-row-item__text">{w("invites.show_hint")}</p>
          <Button variant="secondary" onClick={() => void load()}>
            {w("invites.show")}
          </Button>
        </div>
      )}
      {invites && invites.length > 0 && (
        <ul className="av-rows" role="list" aria-label={w("invites.pending_title")}>
          {invites.map((i) => {
            const days = Math.floor((new Date(i.expires_at).getTime() - Date.now()) / DAY);
            const when = shortDate(i.created_at, locale);
            return (
              <li key={i.id} className="av-row-item av-org-person">
                <span className="av-org-avatar" aria-hidden="true">
                  {initials(i.email)}
                </span>
                <div className="av-row-item__text">
                  <p className="av-field__label">{i.email}</p>
                  <p className="av-muted">{days >= 1 ? w("invites.line", { date: when, days }) : w("invites.line_today", { date: when })}</p>
                </div>
                <div className="av-org-role">
                  <p className="av-field__label">{roleName(i.role)}</p>
                  <p className="av-muted">{w("invites.waiting")}</p>
                </div>
                <Button variant="quiet" aria-label={w("invites.cancel_for", { email: i.email })} onClick={() => void cancel(i)}>
                  {w("invites.cancel")}
                </Button>
              </li>
            );
          })}
        </ul>
      )}
      <MessageNotice message={message} />
      <form className="av-org-invite" onSubmit={(e) => void send(e)} noValidate>
        <TextField label={w("invites.email")} name="email" type="email" autoComplete="off" placeholder={w("invites.email_placeholder")} value={email} onChange={(e) => setEmail(e.target.value)} error={needEmail ? w("invites.email_needed") : undefined} />
        <div className="av-field">
          <label className="av-field__label" htmlFor="invite-role">
            {w("invites.role")}
          </label>
          <select id="invite-role" className="av-input av-org-select" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {roles.map((r) => (
              <option key={r} value={r}>
                {roleName(r)}
              </option>
            ))}
          </select>
        </div>
        <Button type="submit" disabled={busy}>
          {busy ? w("invites.sending") : w("invites.send")}
        </Button>
      </form>
      <p className="av-muted av-small">{w("invites.note")}</p>
    </>
  );
}

