// Local development only: GET /__dev/mfa-code answers the current 6-digit code for the demo
// account, so the "Skip" button can fill it in. The secret comes from DEV_TOTP_SECRET in
// web/.env.local (never committed). Vite serves this only in `pnpm dev`; builds never include it.
import { createHmac } from "node:crypto";
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

export function devMfaPlugin(): Plugin {
  return {
    name: "avodeck-dev-mfa",
    apply: "serve",
    configureServer(server) {
      const secret = loadEnv(server.config.mode, process.cwd(), "DEV_").DEV_TOTP_SECRET;
      server.middlewares.use("/__dev/mfa-code", (_req, res) => {
        res.statusCode = secret ? 200 : 404;
        res.end(secret ? totp(secret) : "");
      });
    },
  };
}
