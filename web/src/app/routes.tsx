import { Navigate, type RouteObject } from "react-router-dom";
import { ConfirmEmail } from "./auth/ConfirmEmail";
import { ResetPassword } from "./auth/ResetPassword";
import { SignIn } from "./auth/SignIn";
import { capabilityCaseRoutes, capabilityOfflineRoutes, capabilityRoutes } from "./capabilityRoutes";
import { CaseLayout } from "./CaseLayout";
import { NotFound } from "./NotFound";
import { RequireSession } from "./RequireSession";
import { Root } from "./Root";

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <Root />,
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
