import type { RouteObject } from "react-router-dom";
import { registerScreen } from "../../foundation/lib/screens";
import { Close } from "./Close";

registerScreen("close", ({ caseId }) => `/cases/${caseId}/close`);

/** S26. Added to app/routes.tsx by the foundation. */
export const closeRoutes: RouteObject[] = [{ path: "cases/:caseId/close", element: <Close /> }];
