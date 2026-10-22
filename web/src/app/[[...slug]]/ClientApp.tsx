"use client";

import dynamic from "next/dynamic";

// The router reads window and the session cookie, so it only ever runs in the browser.
const Spa = dynamic(() => import("../Spa").then((m) => m.Spa), { ssr: false });

export function ClientApp() {
  return <Spa />;
}
