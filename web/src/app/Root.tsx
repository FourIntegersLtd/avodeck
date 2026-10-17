import { useEffect } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { onSignedOut } from "../foundation/platform-client";

const OPEN_PATHS = ["/sign-in", "/reset-password", "/account/email/confirm"];

/** The top of the app: when the API says the session has ended, go back to sign in. */
export function Root() {
  const navigate = useNavigate();
  const location = useLocation();
  useEffect(
    () =>
      onSignedOut(() => {
        if (!OPEN_PATHS.includes(location.pathname)) navigate("/sign-in", { replace: true, state: { from: location.pathname } });
      }),
    [navigate, location.pathname],
  );
  return <Outlet />;
}
