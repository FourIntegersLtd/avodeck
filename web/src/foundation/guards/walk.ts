// Shared file walker for the guard tests (kept beside them so the guards stay self-contained).
import { readdirSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";

export const WEB = resolve(__dirname, "../../..");
export const REPO = resolve(WEB, "..");

export function files(dir: string, keep: (rel: string) => boolean): string[] {
  const out: string[] = [];
  const visit = (d: string) => {
    for (const name of readdirSync(d)) {
      const full = join(d, name);
      if (statSync(full).isDirectory()) {
        if (name !== "node_modules") visit(full);
      } else if (keep(relative(WEB, full).split("\\").join("/"))) {
        out.push(full);
      }
    }
  };
  visit(dir);
  return out;
}

export const isTest = (rel: string) => /\.(test|spec)\.tsx?$/.test(rel) || rel.startsWith("src/test/");
