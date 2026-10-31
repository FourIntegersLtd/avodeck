// Start a team: the team's name and one button. The creator becomes its owner (the API does that).
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { errorMessage } from "../../foundation/lib/errors";
import { screenPath } from "../../foundation/lib/screens";
import { api, unwrap } from "../../foundation/platform-client";
import { BackLink, Button, LinkButton, TextField, useBusy } from "../../foundation/ui";
import { MessageNotice, OWN_ERRORS, type Message } from "./common";
import { w } from "./words";

/** The S28 section: says what a team is and links to the screen. */
export function StartTeamBody() {
  return (
    <div className="av-stack">
      <p>{w("start_team.section_body")}</p>
      <div>
        <LinkButton to={screenPath("organisation_new")} variant="secondary">
          {w("start_team.link")}
        </LinkButton>
      </div>
    </div>
  );
}

export function StartTeam() {
  const navigate = useNavigate();
  const { busy, run } = useBusy();
  const [name, setName] = useState("");
  const [message, setMessage] = useState<Message>(null);
  const start = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return setMessage({ tone: "error", text: w("start_team.name_needed") });
    setMessage(null);
    void run(async () => {
      try {
        const created = await unwrap(api.POST("/v1/organisations", { body: { name: trimmed } }));
        navigate(screenPath("organisation_settings", { params: { organisation_id: created.id } }));
      } catch (err) {
        setMessage({ tone: "error", text: errorMessage(err, OWN_ERRORS) });
      }
    });
  };
  return (
    <div className="av-page av-page--narrow av-stack">
      <BackLink to="/account">{w("account.back")}</BackLink>
      <h1>{w("start_team.title")}</h1>
      <p>{w("start_team.intro")}</p>
      <MessageNotice message={message} />
      <form className="av-stack av-card av-org-card" onSubmit={start} noValidate>
        <TextField label={w("start_team.name")} name="name" autoComplete="off" value={name} onChange={(e) => setName(e.target.value)} />
        <div className="av-row av-org-actions">
          <Button type="submit" busy={busy}>{w("start_team.button")}</Button>
        </div>
      </form>
    </div>
  );
}
