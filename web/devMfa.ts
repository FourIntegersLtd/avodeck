// Local development only, so the code screens pass themselves (src/foundation/lib/DevSkipCode.tsx):
// POST /__dev/mfa-secret keeps an account's authenticator key from setup in web/.dev-mfa.json
// (never committed); GET /__dev/mfa-code?email= answers that account's current 6-digit code, else
// the last key kept, else DEV_TOTP_SECRET from web/.env.local. Vite serves this only in `pnpm dev`;
// builds never include it.
import { createHmac } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { loadEnv, type Plugin } from "vite";

function totp(secret: string, now = Date.now()): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of secret.replace(/=+$/, "").toUpperCase()) bits += alphabet.indexOf(ch).toString(2).padStart(5, "0");
  const key = Buffer.from((bits.match(/.{8}/g) ?? []).map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(now / 30000)));
  const h = createHmac("sha1", key).update(counter).digest();
  const o = h.readUInt8(h.length - 1) & 15;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}

type Kept = { last?: string; accounts: Record<string, string> };

export function devMfaPlugin(): Plugin {
  return {
    name: "avodeck-dev-mfa",
    apply: "serve",
    configureServer(server) {
      const fallback = loadEnv(server.config.mode, process.cwd(), "DEV_").DEV_TOTP_SECRET;
      const file = resolve(server.config.root, ".dev-mfa.json");
      const read = (): Kept => (existsSync(file) ? (JSON.parse(readFileSync(file, "utf8")) as Kept) : { accounts: {} });
      server.middlewares.use("/__dev/mfa-secret", (req, res) => {
        let body = "";
        req.on("data", (chunk) => (body += chunk));
        req.on("end", () => {
          const { email = "", secret = "" } = JSON.parse(body || "{}") as { email?: string; secret?: string };
          const kept = read();
          if (secret) {
            if (email) kept.accounts[email] = secret;
            kept.last = secret;
            writeFileSync(file, JSON.stringify(kept, null, 2));
          }
          res.statusCode = 204;
          res.end();
        });
      });
      server.middlewares.use("/__dev/mfa-code", (req, res) => {
        const email = new URL(req.url ?? "", "http://dev").searchParams.get("email") ?? "";
        const kept = read();
        const secret = (email && kept.accounts[email]) || kept.last || fallback;
        res.statusCode = secret ? 200 : 404;
        res.end(secret ? totp(secret) : "");
      });
    },
  };
}
