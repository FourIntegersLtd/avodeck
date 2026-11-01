import type { RouteObject } from "react-router-dom";
import { Account } from "./Account";
import { LegalGate } from "./legal/LegalGate";
import { Messages } from "./Messages";

export const accountRoutes: RouteObject[] = [
  { path: "account", element: <Account /> },
  { path: "messages", element: <Messages /> },
  { path: "agree", element: <LegalGate /> },
];
