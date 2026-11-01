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

/** The company's own legal agreements (terms, privacy notice, data processing agreement). They are
 * contract text, versioned and accepted as written, and name the governing law and the parties, so
 * they cannot be pack vocabulary or split into i18n keys without changing what the person agrees to.
 * Exactly these three files are exempt from the forbidden-words and hard-coded-text scans; nothing
 * else under account/legal (LegalGate.tsx, which is app UI) is. */
export const LEGAL_AGREEMENT_FILES = new Set([
  "src/capabilities/account/legal/TermsContent.tsx",
  "src/capabilities/account/legal/PrivacyContent.tsx",
  "src/capabilities/account/legal/DpaContent.tsx",
]);
