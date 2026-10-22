import { NextResponse, type NextRequest } from "next/server";
import { buildPolicy, newNonce } from "./foundation/security/csp";

// Gives every page its own Content Security Policy and nonce. Next reads the nonce off the request's
// policy header and stamps it onto its own scripts, so the page loads with no inline-script allowance.
export function proxy(request: NextRequest) {
  const nonce = newNonce();
  const policy = buildPolicy(nonce, {
    apiOrigin: process.env.NEXT_PUBLIC_API_ORIGIN ?? "",
    isDev: process.env.NODE_ENV === "development",
  });
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", policy);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", policy);
  return response;
}

export const config = {
  matcher: [{ source: "/((?!_next/static|_next/image|favicon.svg).*)", missing: [{ type: "header", key: "next-router-prefetch" }] }],
};
