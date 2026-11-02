import type { RouteObject } from "react-router-dom";
import { lazyScreen } from "../../foundation/lib/lazyScreen";
import { registerScreen } from "../../foundation/lib/screens";

registerScreen("close", ({ caseId }) => `/cases/${caseId}/close`);

/** S26. Added to app/routes.tsx by the foundation. */
export const closeRoutes: RouteObject[] = [{ path: "cases/:caseId/close", ...lazyScreen(() => import("./Close"), ({ Close }) => <Close />) }];
