// web/AGENTS.md: auth is a cookie set by FastAPI. The web app never writes tokens to
// localStorage or sessionStorage, and never writes cookies itself. The one exception (web/AGENTS.md,
// screen 25): the sealed witness-box copy may be kept in IndexedDB, by its own module only, and that
// module may touch no other storage and no token. The second exception: the running upload's small record
// (ids, size, hash, keys, expiry; never a name, contents or a token) may sit in localStorage, by
// `uploadStore.ts` only, so a refresh can pick the upload up again.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { apiError, http, json, server, signedIn } from "../../test/api";
import { renderAt } from "../../test/render";
import { files, isTest, WEB } from "./walk";

const STORAGE = /\b(localStorage|sessionStorage|indexedDB)\b|document\.cookie\s*=(?!=)/;
const NOT_INDEXED_DB = /\b(localStorage|sessionStorage)\b|document\.cookie\s*=(?!=)|token/i;
const NOT_LOCAL_ONLY = /\b(sessionStorage|indexedDB)\b|document\.cookie\s*=(?!=)|token|filename|\.name\b/i;
/** The only module allowed localStorage: it keeps the running upload's record, and nothing else. */
const UPLOAD_STORE = "src/foundation/lib/uploadStore.ts";
/** The only module allowed IndexedDB: it keeps the S25 copy, still sealed, on the device. */
const WITNESS_BOX_STORE = "src/capabilities/trial/witnessBoxStore.ts";

/** The sources (paths relative to web/) that break the storage rule. */
function storageHits(sources: { rel: string; text: string }[]): string[] {
  const rule = (rel: string) => (rel === WITNESS_BOX_STORE ? NOT_INDEXED_DB : rel === UPLOAD_STORE ? NOT_LOCAL_ONLY : STORAGE);
  return sources.filter(({ rel, text }) => rule(rel).test(text)).map((s) => s.rel);
}

afterEach(() => vi.restoreAllMocks());

test("no source file touches browser storage or writes a cookie", () => {
  const src = files(join(WEB, "src"), (rel) => /\.(ts|tsx)$/.test(rel) && !isTest(rel) && !rel.startsWith("src/foundation/guards/"));
  expect(storageHits(src.map((p) => ({ rel: p.slice(WEB.length + 1), text: readFileSync(p, "utf8") })))).toEqual([]);
});

test("only the witness-box copy's own module may use IndexedDB, and for nothing else", () => {
  const refused = [
    { rel: "src/capabilities/trial/offline.ts", text: "indexedDB.open('avodeck-witness-box')" },
    { rel: "src/foundation/lib/session.ts", text: "indexedDB.open('session')" },
    { rel: "src/capabilities/trial/witnessBoxStore.ts.bak", text: "indexedDB.open('x')" },
    { rel: WITNESS_BOX_STORE, text: "indexedDB.open('x'); localStorage.setItem('a', 'b')" },
    { rel: WITNESS_BOX_STORE, text: "indexedDB.open('x'); sessionStorage.getItem('a')" },
    { rel: WITNESS_BOX_STORE, text: "indexedDB.open('x'); document.cookie = 'a=b'" },
    { rel: WITNESS_BOX_STORE, text: "const accessToken = 'x'; indexedDB.open('x')" },
    { rel: "src/app/routes.tsx", text: "localStorage.setItem('token', 'x')" },
  ];
  expect(storageHits(refused)).toEqual(refused.map((s) => s.rel));
  expect(storageHits([{ rel: WITNESS_BOX_STORE, text: "indexedDB.open('avodeck-witness-box', 1)" }])).toEqual([]);
});

test("only the upload record's own module may use localStorage, and for nothing else", () => {
  const refused = [
    { rel: "src/foundation/lib/session.tsx", text: "localStorage.setItem('a', 'b')" },
    { rel: "src/foundation/lib/uploadStore.tsx", text: "localStorage.setItem('a', 'b')" },
    { rel: UPLOAD_STORE, text: "sessionStorage.setItem('a', 'b')" },
    { rel: UPLOAD_STORE, text: "localStorage.setItem('a', accessToken)" },
    { rel: UPLOAD_STORE, text: "localStorage.setItem('a', file.name)" },
    { rel: UPLOAD_STORE, text: "document.cookie = 'a=b'" },
  ];
  expect(storageHits(refused)).toEqual(refused.map((s) => s.rel));
  expect(storageHits([{ rel: UPLOAD_STORE, text: "localStorage.setItem(key, JSON.stringify({ size }))" }])).toEqual([]);
});

test("signing in with a password and a code stores nothing in the browser", async () => {
  const setItem = vi.spyOn(Storage.prototype, "setItem");
  const cookie = Object.getOwnPropertyDescriptor(Document.prototype, "cookie")!;
  const cookieWrites: string[] = [];
  vi.spyOn(document, "cookie", "set").mockImplementation((value: string) => {
    cookieWrites.push(value);
    cookie.set!.call(document, value);
  });
  server.use(
    http.get("/v1/auth/session", () => apiError(401, "not_signed_in")),
    http.post("/v1/auth/sign-in", () => json({ status: "mfa_required", mfa_enrolled: true })),
    http.post("/v1/auth/mfa/verify", () => {
      signedIn();
      return json({ status: "signed_in", recovery_codes: null });
    }),
  );
  const { user } = renderAt("/sign-in");
  await user.type(await screen.findByLabelText("Email address"), "j.morgan@example.test");
  await user.type(screen.getByLabelText("Password"), "a long enough password");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  await user.type(await screen.findByLabelText("6-digit code"), "123456");
  await user.click(screen.getByRole("button", { name: "Carry on" }));
  await waitFor(() => expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/Good (morning|afternoon|evening)/));
  expect(setItem).not.toHaveBeenCalled();
  expect(cookieWrites).toEqual([]);
  expect(localStorage.length + sessionStorage.length).toBe(0);
});
