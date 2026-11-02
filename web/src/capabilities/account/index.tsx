import type { RouteObject } from "react-router-dom";
import { lazyScreen } from "../../foundation/lib/lazyScreen";

export const accountRoutes: RouteObject[] = [
  { path: "account", ...lazyScreen(() => import("./Account"), ({ Account }) => <Account />) },
  { path: "messages", ...lazyScreen(() => import("./Messages"), ({ Messages }) => <Messages />) },
  { path: "agree", ...lazyScreen(() => import("./legal/LegalGate"), ({ LegalGate }) => <LegalGate />) },
];
