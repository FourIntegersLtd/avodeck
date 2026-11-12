// Format version 1 of the copy for the witness box, as the API builds it (api/app/features/trial/routes_offline.py).
export interface CopyPage {
  page_no: number;
  trial_page_no: number | null;
}

export interface CopyContent {
  format_version: number;
  case_id: string;
  built_at: string;
  date_order: string;
  court_bundle: string;
  /** What the copy was built from (the reader ignores it; a later About area may show it). */
  sources?: {
    court_bundle: { id: string; sha256: string; version: number } | null;
    report: { version_id: string; version_no: number; sha256: string | null };
    pack_versions: Record<string, string>;
    built_at: string;
  };
  case_label?: string | null;
  source_types?: { key: string; label: string; colour: string | null }[];
  report: { version_no: number; served_on: string | null; paragraphs: { para_no: number; section: string; text: string; pages: CopyPage[] }[] } | null;
  facts: { kind: string; text: string; date: string | null; page_no: number | null; trial_page_no: number | null; source_type?: string | null }[];
  concordance: { page_no: number; trial_page_no: number | null; status: string }[];
  joint_statement: {
    type: string;
    status: string;
    items: { number: number; text: string; own_position: string | null; agreed: boolean | null; reason: string | null }[];
  } | null;
  answers: { question_no: number; text: string }[];
}
