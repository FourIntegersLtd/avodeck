// A person signed in with a temporary password from Avodeck support chooses their own before anything else
// (ADR 0024). The server decides: the session says `must_change_password`, and any other call answers 403
// `password_change_required`. The screen asks for the given password and a new one, in plain words.
import { screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";
import { apiError, http, json, server, signedIn } from "../test/api";
import { me, sessionStatus } from "../test/fixtures";
import { axeViolations, renderAt } from "../test/render";

function mustChange() {
  signedIn();
  let changed = false;
  let sent: unknown = null;
  server.use(
    http.get("/v1/auth/session", () => json(sessionStatus({ must_change_password: !changed }))),
    http.get("/v1/me", () => json({ ...me(), must_change_password: !changed })),
    http.get("/v1/today", () => (changed ? json({ items: [], cases: [] }) : apiError(403, "password_change_required"))),
    http.post("/v1/auth/password/change", async ({ request }) => {
      sent = await request.json();
      changed = true;
      return json({ status: "password_changed" });
    }),
  );
  return { sent: () => sent };
}

test("a temporary password asks for the person's own before anything else", async () => {
  const api = mustChange();
  const { user, router } = renderAt("/today");
  expect(await screen.findByRole("heading", { level: 1, name: "Choose your own password" })).toBeInTheDocument();
  expect(screen.queryByRole("navigation")).toBeNull();
  await user.type(screen.getByLabelText("The password you were given"), "ABCD-efgh-2345-JKLM-npqr");
  await user.type(screen.getByLabelText("Your new password"), "My own long password 7");
  await user.click(screen.getByRole("button", { name: "Save my password" }));
  await waitFor(() => expect(screen.queryByRole("heading", { name: "Choose your own password" })).toBeNull());
  expect(api.sent()).toEqual({ current_password: "ABCD-efgh-2345-JKLM-npqr", new_password: "My own long password 7" });
  expect(router.state.location.pathname).toBe("/today");
});

test("any screen the server refuses for a temporary password shows the same step", async () => {
  signedIn();
  server.use(http.get("/v1/today", () => apiError(403, "password_change_required")));
  renderAt("/today");
  expect(await screen.findByRole("heading", { level: 1, name: "Choose your own password" })).toBeInTheDocument();
});

test.each([
  ["wrong_password", 400, "That is not the password you were given. Check it and try again."],
  ["same_password", 422, "Your new password must be different from the one you were given."],
  ["invalid", 422, "Please use at least 12 characters."],
])("explains %s in plain words", async (code, status, words) => {
  mustChange();
  server.use(http.post("/v1/auth/password/change", () => apiError(status, code)));
  const { user } = renderAt("/today");
  await user.type(await screen.findByLabelText("The password you were given"), "ABCD-efgh-2345-JKLM-npqr");
  await user.type(screen.getByLabelText("Your new password"), "short");
  await user.click(screen.getByRole("button", { name: "Save my password" }));
  expect(await screen.findByText(words)).toBeInTheDocument();
  expect(screen.queryByText(code)).toBeNull();
});

test("the person can sign out instead", async () => {
  mustChange();
  renderAt("/today");
  expect(await screen.findByRole("button", { name: "Sign out" })).toBeInTheDocument();
});

test("has no accessibility problems", async () => {
  mustChange();
  const { container } = renderAt("/today");
  await screen.findByRole("heading", { level: 1, name: "Choose your own password" });
  expect(await axeViolations(container)).toEqual([]);
});
