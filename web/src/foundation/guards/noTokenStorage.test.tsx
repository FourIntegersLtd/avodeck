// web/AGENTS.md: auth is a cookie set by FastAPI. The web app never writes tokens to
// localStorage or sessionStorage, and never writes cookies itself.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { apiError, http, json, server, signedIn } from "../../test/api";
import { renderAt } from "../../test/render";
import { files, isTest, WEB } from "./walk";

const STORAGE = /\b(localStorage|sessionStorage|indexedDB)\b|document\.cookie\s*=(?!=)/;

afterEach(() => vi.restoreAllMocks());

test("no source file touches browser storage or writes a cookie", () => {
  const src = files(join(WEB, "src"), (rel) => /\.(ts|tsx)$/.test(rel) && !isTest(rel) && !rel.startsWith("src/foundation/guards/"));
  const hits = src.filter((p) => STORAGE.test(readFileSync(p, "utf8"))).map((p) => p.slice(WEB.length + 1));
  expect(hits).toEqual([]);
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
