import type { NextConfig } from "next";

// The browser talks only to FastAPI (rule 4). Next serves the screens and forwards /v1 to the API,
// so the page and the API share one origin and the session cookie (web/AGENTS.md, "Using the shell").
const API_ORIGIN = process.env.API_DEV_ORIGIN ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  poweredByHeader: false,
  typescript: { tsconfigPath: "tsconfig.next.json" },
  async rewrites() {
    return [{ source: "/v1/:path*", destination: `${API_ORIGIN}/v1/:path*` }];
  },
};

export default config;
