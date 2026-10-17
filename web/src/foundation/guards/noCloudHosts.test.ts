// @vitest-environment node
// Rule 4: the browser talks only to FastAPI. No Supabase, Azure or AWS hosts, keys or SDKs in the
// frontend source or build, and the Content Security Policy lets the page connect only to the API.
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { build } from "vite";
import { beforeAll, describe, expect, test } from "vitest";
import { files, WEB } from "./walk";

const FORBIDDEN = /supabase\.co|supabase\.in|@supabase\/|cognitiveservices\.azure\.com|\.azure\.com|amazonaws\.com|bedrock|SUPABASE_ANON|SUPABASE_URL/i;
const SDKS = [/^@supabase\//, /^@azure\//, /^aws-sdk$/, /^@aws-sdk\//, /^aws-amplify/, /^firebase/];
const API = "https://uk.api.example.test";

function offenders(paths: string[]) {
  return paths.filter((p) => FORBIDDEN.test(readFileSync(p, "utf8"))).map((p) => p.slice(WEB.length + 1));
}

test("package.json depends on no cloud SDK", () => {
  const pkg = JSON.parse(readFileSync(join(WEB, "package.json"), "utf8"));
  const names = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies });
  expect(names.filter((n) => SDKS.some((re) => re.test(n)))).toEqual([]);
});

test("the source names no cloud host or key", () => {
  const src = files(join(WEB, "src"), (rel) => !rel.startsWith("src/foundation/guards/"));
  expect(src.length).toBeGreaterThan(0);
  expect(offenders(src)).toEqual([]);
});

test("the host pattern catches a planted host", () => {
  expect(FORBIDDEN.test('fetch("https://abc.supabase.co/rest/v1")')).toBe(true);
  expect(FORBIDDEN.test("https://uksouth.api.cognitive.microsoft.com.azure.com")).toBe(true);
});

describe("the production build", () => {
  let out = "";
  beforeAll(async () => {
    out = mkdtempSync(join(tmpdir(), "avodeck-build-"));
    process.env.VITE_API_ORIGIN = API;
    try {
      await build({ root: WEB, logLevel: "silent", build: { outDir: out, emptyOutDir: true } });
    } finally {
      delete process.env.VITE_API_ORIGIN;
    }
  }, 180_000);

  test("contains no cloud host, key or SDK", () => {
    const built = files(out, () => true);
    expect(built.length).toBeGreaterThan(1);
    expect(offenders(built)).toEqual([]);
  });

  test("ships a Content Security Policy that allows only the API origin", () => {
    const html = readFileSync(join(out, "index.html"), "utf8");
    const meta = html.match(/<meta[^>]+http-equiv="Content-Security-Policy"[^>]+content="([^"]+)"/i);
    expect(meta, "index.html has a CSP meta tag").not.toBeNull();
    const policy = new Map(
      meta![1]!
        .split(";")
        .map((d) => d.trim().split(/\s+/))
        .filter((parts) => parts[0])
        .map(([name, ...sources]) => [name!, sources]),
    );
    expect(policy.get("default-src")).toEqual(["'self'"]);
    expect(new Set(policy.get("connect-src"))).toEqual(new Set(["'self'", API, "wss://uk.api.example.test"]));
    expect(policy.get("script-src")).toEqual(["'self'"]);
    expect(policy.get("object-src")).toEqual(["'none'"]);
    expect(policy.get("base-uri")).toEqual(["'none'"]);
    const allowed = new Set(["'self'", "'none'", "data:", "blob:", API, "wss://uk.api.example.test"]);
    for (const [name, sources] of policy) {
      for (const source of sources) expect(allowed.has(source), `${name} ${source}`).toBe(true);
    }
  });
});
