// Slice B (reader): screens 09 (Reading) and 10 (Pages we couldn't read), inside a case.
import type { RouteObject } from "react-router-dom";
import { lazyScreen } from "../../foundation/lib/lazyScreen";
import { registerScreen } from "../../foundation/lib/screens";
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
  { path: "read", ...lazyScreen(() => import("./Reader"), ({ Reader }) => <Reader />), handle: { screen: "reader" } },
  { path: "read/transcript", ...lazyScreen(() => import("./TranscriptRead"), ({ TranscriptRead }) => <TranscriptRead />), handle: { screen: "reader" } },
  { path: "read/:pageNo", ...lazyScreen(() => import("./Reader"), ({ Reader }) => <Reader />), handle: { screen: "reader" } },
  { path: "read/couldnt-read", ...lazyScreen(() => import("./CouldntRead"), ({ CouldntRead }) => <CouldntRead />), handle: { screen: "couldnt_read" } },
];
