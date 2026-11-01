// Account-level legal content (terms, privacy policy, DPA) is not tied to a case, so it cannot read
// `GET /cases/{id}/pack`. It reads the words that change by jurisdiction (company registration place,
// registered office address) from `GET /v1/jurisdictions/{code}` instead (rule 11; web/AGENTS.md "Copy").
// The cell offers exactly one jurisdiction at launch, so the first one listed is the company's own.
import { useResource } from "../../../foundation/lib/useResource";
import { api, unwrap } from "../../../foundation/platform-client";

export interface LegalVocabulary {
  company_registration_place?: string;
  company_registered_office?: string;
}

async function loadLegalVocabulary(): Promise<LegalVocabulary> {
  const jurisdictions = await unwrap(api.GET("/v1/jurisdictions"));
  const code = jurisdictions[0]?.code;
  if (!code) return {};
  const summary = await unwrap(api.GET("/v1/jurisdictions/{code}", { params: { path: { code } } }));
  return (summary.vocabulary ?? {}) as LegalVocabulary;
}

/** The pack-driven words the legal panels need. Empty (not yet loaded) renders them blank for an
 * instant rather than falling back to a hard-coded word. */
export function useLegalVocabulary(): LegalVocabulary {
  const { data } = useResource(loadLegalVocabulary, [], { cache: "account/legal#vocabulary" });
  return data ?? {};
}
