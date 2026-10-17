import { useParams } from "react-router-dom";
import { t } from "../foundation/i18n";
import { useResource } from "../foundation/lib/useResource";
import { api, unwrap } from "../foundation/platform-client";
import { CaseHeader, LinkButton, LoadError, Loading, Notice } from "../foundation/ui";

/** A case's frame: its name and the pack's steps. Slices fill the screens inside it. */
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
  if (found.error) return <div className="av-page"><LoadError onRetry={found.reload} /></div>;
  if (!found.data) return <div className="av-page"><Loading /></div>;
  return (
    <>
      <CaseHeader steps={found.data.pack.workflow_steps as Array<{ key: string }>} title={<h1>{found.data.item.label}</h1>} />
      <div className="av-page av-stack">
        <Notice>
          <p>{t("app.case.coming")}</p>
        </Notice>
        <LinkButton to="/today" variant="secondary">
          {t("app.case.back")}
        </LinkButton>
      </div>
    </>
  );
}
