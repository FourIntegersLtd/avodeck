import { matchRoutes, Navigate, type RouteObject } from "react-router-dom";
import { setRoutePrefetcher } from "../foundation/lib/routePrefetch";
import { ConfirmEmail } from "./auth/ConfirmEmail";
import { ResetPassword } from "./auth/ResetPassword";
import { SignIn } from "./auth/SignIn";
import { capabilityCaseRoutes, capabilityOfflineRoutes, capabilityRoutes } from "./capabilityRoutes";
import { CaseLayout } from "./CaseLayout";
import { NotFound } from "./NotFound";
import { RequireSession } from "./RequireSession";
import { RouteError } from "./RouteError";
import { Root } from "./Root";

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <Root />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <Navigate to="/today" replace /> },
      { path: "sign-in", element: <SignIn /> },
      { path: "reset-password", element: <ResetPassword /> },
      { path: "account/email/confirm", element: <ConfirmEmail /> },
      // Screens that work from a copy on the device, with no sign-in and no API (capabilityRoutes.ts).
      ...capabilityOfflineRoutes,
      {
        element: <RequireSession />,
        // Capability routes come from each src/capabilities/<feature>/index.tsx (capabilityRoutes.ts).
        children: [
          ...capabilityRoutes,
          { path: "cases/:caseId", element: <CaseLayout />, children: capabilityCaseRoutes },
        ],
      },
      { path: "*", element: <NotFound /> },
    ],
  },
];

/** Start the download of the screens behind a path (the nav calls this on hover, focus and touch). */
export function prefetchPath(path: string) {
  for (const match of matchRoutes(routes, path) ?? []) {
    const load = match.route.lazy as (() => Promise<unknown>) | undefined;
    if (typeof load === "function") void load().catch(() => undefined);
  }
}

setRoutePrefetcher(prefetchPath);
