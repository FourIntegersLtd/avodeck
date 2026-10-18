import { useContext } from "react";
import { useMatches, useOutlet, useParams } from "react-router-dom";
import { formatDue, humanise, t } from "../foundation/i18n";
import { stepFor, type CaseFrame, type CaseScreenHandle } from "../foundation/lib/caseFrame";
import { SessionContext } from "../foundation/lib/session";
import { useTimeHeartbeat } from "../foundation/lib/timeHeartbeat";
import { useResource } from "../foundation/lib/useResource";
import { api, unwrap } from "../foundation/platform-client";
import { CaseHeader, LinkButton, LoadError, Loading, Notice } from "../foundation/ui";

/** A case's frame: its name and the pack's steps. Slices' case routes render inside it (the outlet),
 * get the case and pack from `useCaseFrame()`, and name their screen key in the route's handle. */
export function CaseLayout() {
  const { caseId = "" } = useParams();
  const found = useResource(
    async () => {
      const path = { params: { path: { case_id: caseId } } };
      const [item, pack] = await Promise.all([unwrap(api.GET("/v1/cases/{case_id}", path)), unwrap(api.GET("/v1/cases/{case_id}/pack", path))]);
      return { item, pack };
    },
    [caseId],
  );
  const frame: CaseFrame | undefined = found.data && { ...found.data, reload: found.reload };
  const outlet = useOutlet(frame);
  const screenKey = useMatches()
    .map((m) => (m.handle as Partial<CaseScreenHandle> | undefined)?.screen)
    .filter(Boolean)
    .pop();
  // Time is kept on every screen a pack step holds; the server decides what counts and which step it is.
  useTimeHeartbeat(caseId, found.data && screenKey && stepFor(found.data.pack, screenKey) ? screenKey : null);
  const me = useContext(SessionContext)?.me;
  const person = me && (me.person.full_name || me.person.email);
  // While the case loads (or fails to) the bar still shows the logo and the avatar: it is the only bar.
  if (found.error || !found.data) {
    return (
      <>
        <CaseHeader bar steps={[]} person={person} />
        <div className="av-page">{found.error ? <LoadError onRetry={found.reload} /> : <Loading />}</div>
      </>
    );
  }
  const { item, pack } = found.data;
  const type = item.case_type ? humanise(item.case_type) : "";
  return (
    <>
      <CaseHeader
        bar
        caseId={caseId}
        steps={pack.workflow_steps as Array<{ key: string; screens?: string[] }>}
        current={stepFor(pack, screenKey)}
        title={<h1 className="av-case-bar__title">{item.label}</h1>}
        subtitle={item.due_date && type ? t("ui.case_bar.sub", { type, date: formatDue(item.due_date, pack.locale) }) : type}
        person={person}
      />
      {outlet ?? (
        <div className="av-page av-stack">
          <Notice>
            <p>{t("app.case.coming")}</p>
          </Notice>
          <LinkButton to="/today" variant="secondary">
            {t("app.case.back")}
          </LinkButton>
        </div>
      )}
    </>
  );
}
