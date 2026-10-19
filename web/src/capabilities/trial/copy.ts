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
  report: { version_no: number; served_on: string | null; paragraphs: { para_no: number; section: string; text: string; pages: CopyPage[] }[] } | null;
  facts: { kind: string; text: string; date: string | null; page_no: number | null; trial_page_no: number | null }[];
  concordance: { page_no: number; trial_page_no: number | null; status: string }[];
  joint_statement: {
    type: string;
    status: string;
    items: { number: number; text: string; own_position: string | null; agreed: boolean | null; reason: string | null }[];
  } | null;
  answers: { question_no: number; text: string }[];
}
