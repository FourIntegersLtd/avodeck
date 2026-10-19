import { useContext } from "react";
import { Navigate, useMatches, useOutlet, useParams } from "react-router-dom";
import { t } from "../foundation/i18n";
import { caseSubtitle, stepFor, type CaseFrame, type CaseScreenHandle } from "../foundation/lib/caseFrame";
import { screenPath } from "../foundation/lib/screens";
import { SessionContext } from "../foundation/lib/session";
import { useTimeHeartbeat } from "../foundation/lib/timeHeartbeat";
import { useResource } from "../foundation/lib/useResource";
import { api, unwrap } from "../foundation/platform-client";
import { CaseHeader, LinkButton, LoadError, Loading, Notice } from "../foundation/ui";
import { WhereYouAre } from "./WhereYouAre";

/** A case's frame: its name and the pack's steps. Slices' case routes render inside it (the outlet),
 * get the case and pack from `useCaseFrame()`, and name their screen key in the route's handle. */
/** Where a bare case address opens: the first registered screen inside the case of the step the case is at. */
export function caseHome(frame: Pick<CaseFrame, "item" | "pack">): string | null {
  const stage = (frame.item as { stage?: string | null }).stage;
  const steps = frame.pack.workflow_steps as Array<{ key: string; screens?: string[] }>;
  const inside = `/cases/${frame.item.id}/`;
  for (const key of steps.find((s) => s.key === stage)?.screens ?? []) {
    const path = screenPath(key, { caseId: frame.item.id });
    if (path?.startsWith(inside)) return path;
  }
  return null;
}

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
  // Per-step progress for every pill, counted by the server from the case's own records.
  const stepProgress = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/steps/progress", { params: { path: { case_id: caseId } } })), [caseId, screenKey]);
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
  const home = outlet ? null : caseHome(found.data);
  if (home) return <Navigate to={home} replace />;
  const pills = Object.fromEntries(
    (stepProgress.data?.steps ?? []).flatMap((s) => (s.percent === null || s.done === null || s.total === null ? [] : [[s.key, { done: s.done, total: s.total, percent: s.percent }]])),
  );
  const counts = progress.data;
  return (
    <>
      <CaseHeader
        bar
        caseId={caseId}
        steps={pack.workflow_steps as Array<{ key: string; screens?: string[] }>}
        current={stepFor(pack, screenKey)}
        title={<h1 className="av-case-bar__title">{item.label}</h1>}
        subtitle={caseSubtitle(item, pack)}
        progress={pills}
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
