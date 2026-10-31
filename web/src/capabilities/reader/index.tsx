// Slice B (reader): screens 09 (Reading) and 10 (Pages we couldn't read), inside a case.
import type { RouteObject } from "react-router-dom";
import { registerScreen } from "../../foundation/lib/screens";
import { CouldntRead } from "./CouldntRead";
import { Reader } from "./Reader";
import { TranscriptRead } from "./TranscriptRead";
import "./reader.css";
import "./words";

const read = ({ caseId, params }: { caseId: string; params: Record<string, unknown> }) =>
  `/cases/${caseId}/read${params?.page ? `/${String(params.page)}` : ""}`;
const unreadable = ({ caseId }: { caseId: string }) => `/cases/${caseId}/read/couldnt-read`;

registerScreen("S09", read);
registerScreen("S10", unreadable);
registerScreen("reader", read);
registerScreen("couldnt_read", unreadable);

export const readerCaseRoutes: RouteObject[] = [
  { path: "read", element: <Reader />, handle: { screen: "reader" } },
  { path: "read/transcript", element: <TranscriptRead />, handle: { screen: "reader" } },
  { path: "read/:pageNo", element: <Reader />, handle: { screen: "reader" } },
  { path: "read/couldnt-read", element: <CouldntRead />, handle: { screen: "couldnt_read" } },
];
