"use client";

import { useState } from "react";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { AppProviders } from "./AppProviders";
import { routes } from "./routes";

/** The existing react-router app, mounted inside Next while screens are moved to Next routes. */
export function Spa() {
  const [router] = useState(() => createBrowserRouter(routes));
  return (
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>
  );
}
