// Local development only: serve the landing page (../site) beside the app on one origin, as Vercel
// does in production (web/vercel.json, scripts/add-landing.sh): / and /privacy are the landing
// page, everything else is the app. Vite serves this only in `pnpm dev`; builds never include it.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Plugin } from "vite";

const SITE = resolve(__dirname, "../site");
const FILES: Record<string, [string, string]> = {
  "/": ["index.html", "text/html; charset=utf-8"],
  "/index.html": ["index.html", "text/html; charset=utf-8"],
  "/privacy": ["privacy.html", "text/html; charset=utf-8"],
  "/styles.css": ["styles.css", "text/css; charset=utf-8"],
  "/favicon.svg": ["favicon.svg", "image/svg+xml"],
};

export function devLandingPlugin(): Plugin {
  return {
    name: "avodeck-dev-landing",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const file = FILES[(req.url ?? "").split("?")[0] ?? ""];
        if (!file || req.method !== "GET") return next();
        res.setHeader("content-type", file[1]);
        res.end(readFileSync(resolve(SITE, file[0])));
      });
    },
  };
}
