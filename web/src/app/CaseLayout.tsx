import { useContext } from "react";
import { useMatches, useOutlet, useParams } from "react-router-dom";
import { t } from "../foundation/i18n";
import { caseSubtitle, stepFor, type CaseFrame, type CaseScreenHandle } from "../foundation/lib/caseFrame";
import { SessionContext } from "../foundation/lib/session";
import { useTimeHeartbeat } from "../foundation/lib/timeHeartbeat";
import { useResource } from "../foundation/lib/useResource";
import { api, unwrap } from "../foundation/platform-client";
import { CaseHeader, LinkButton, LoadError, Loading, Notice } from "../foundation/ui";
import { WhereYouAre } from "./WhereYouAre";

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
  // Pages read so far: shown on the reading step and on the case home. The bar is fine without it.
  const progress = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/progress", { params: { path: { case_id: caseId } } })), [caseId, screenKey]);
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
  const readStep = stepFor(pack, "reader");
  const counts = progress.data;
  const percent = counts && counts.total > 0 ? Math.round((counts.read / counts.total) * 100) : undefined;
  return (
    <>
      <CaseHeader
        bar
        caseId={caseId}
        steps={pack.workflow_steps as Array<{ key: string; screens?: string[] }>}
        current={stepFor(pack, screenKey)}
        title={<h1 className="av-case-bar__title">{item.label}</h1>}
        subtitle={caseSubtitle(item, pack)}
        progress={readStep && percent !== undefined ? { [readStep]: percent } : undefined}
        person={person}
      />
      {outlet ?? (
        <div className="av-page av-stack">
          {counts && <WhereYouAre caseId={caseId} read={counts.read} total={counts.total} locale={pack.locale} />}
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
