// Support creates an account for a new person at /support/accounts: a name and an email, nothing else (a
// team's own owners and admins invite people to a team).
// Avodeck makes a temporary password (ADR 0024): shown once, in a large box with a copy button, to tell the
// person by phone or in person. They choose their own at first sign-in. No support action asks for a new code.
import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { apiError, http, json, server, signedIn } from "../../test/api";
import { axeViolations, renderAt } from "../../test/render";

vi.setConfig({ testTimeout: 30_000 });

const AT = "/support/accounts";
const account = (over: Record<string, unknown> = {}) => ({
  id: "00000000-0000-4000-8000-0000000000a1",
  person_id: "00000000-0000-4000-8000-0000000000b1",
  email: "new.expert@example.test",
  status: "invited",
  created_at: "2026-10-24T09:00:00Z",
  last_sent_at: "2026-10-24T09:00:00Z",
  ...over,
});
const PASSWORD = "ABCD-efgh-2345-JKLM-npqr";
const list = (accounts: unknown[]) => http.get("/v1/support/accounts", () => json({ accounts }));

async function fill(user: ReturnType<typeof renderAt>["user"], email = "new.expert@example.test") {
  await user.type(await screen.findByLabelText("Full name"), "Dr New Expert");
  await user.type(screen.getByLabelText("Email"), email);
}

test("someone who is not staff sees the not found page", async () => {
  signedIn();
  server.use(http.get("/v1/support/accounts", () => apiError(404, "not_found")));
  renderAt(AT);
  expect(await screen.findByRole("heading", { level: 1 })).not.toHaveTextContent(/account/i);
});

test("the form asks for a name and email, never a password, with one main button", async () => {
  signedIn();
  server.use(list([]));
  renderAt(AT);
  await screen.findByRole("heading", { level: 1, name: "Create an account" });
  expect(screen.getByLabelText("Full name")).toBeInTheDocument();
  expect(screen.getByLabelText("Email")).toBeInTheDocument();
  expect(screen.queryByLabelText(/password/i)).toBeNull();
  expect(document.querySelectorAll("input[type=password]")).toHaveLength(0);
  const primaries = document.querySelectorAll(".av-button:not(.av-button--secondary):not(.av-button--outline):not(.av-button--quiet):not(.av-button--link):not(.av-button--danger)");
  expect(primaries).toHaveLength(1);
  expect(screen.getByText(/temporary password/i)).toBeInTheDocument();
  expect(screen.queryByLabelText(/team/i)).toBeNull();
  expect(screen.queryByLabelText(/role/i)).toBeNull();
});

test("says plainly to tell the password by phone or in person, not by email", async () => {
  signedIn();
  server.use(list([]));
  renderAt(AT);
  await screen.findByRole("heading", { level: 1, name: "Create an account" });
  expect(screen.getByText(/by phone or in person, never by email/)).toBeInTheDocument();
});

test("creates an account with an idempotency key and shows its temporary password once", async () => {
  signedIn();
  let sent: { body: unknown; key: string | null } | null = null;
  server.use(
    list([]),
    http.post("/v1/support/accounts", async ({ request }) => {
      sent = { body: await request.json(), key: request.headers.get("Idempotency-Key") };
      return json(account({ temporary_password: PASSWORD, password_shown: false }), 201);
    }),
  );
  const { user } = renderAt(AT);
  await fill(user);
  await user.click(screen.getByRole("button", { name: "Create the account" }));
  const box = await screen.findByRole("region", { name: "Temporary password for new.expert@example.test" });
  expect(within(box).getByText(PASSWORD)).toHaveClass("av-mono");
  expect(within(box).getByText("Write it down or copy it now. It won't be shown again. Tell it to them by phone or in person, not by email.")).toBeInTheDocument();
  await user.click(within(box).getByRole("button", { name: "Copy the password" }));
  expect(await navigator.clipboard.readText()).toBe(PASSWORD);
  expect(await within(box).findByText("Copied")).toBeInTheDocument();
  expect(sent!.body).toEqual({ full_name: "Dr New Expert", email: "new.expert@example.test" });
  expect(sent!.key).toBeTruthy();
  const row = within(screen.getByRole("list", { name: "Accounts you've created" })).getByRole("listitem");
  expect(row).toHaveTextContent("new.expert@example.test");
  expect(row).toHaveTextContent("Invited");
  expect(row).not.toHaveTextContent(PASSWORD);
});

test("a repeated request that no longer has the password says so", async () => {
  signedIn();
  server.use(list([]), http.post("/v1/support/accounts", () => json(account({ temporary_password: null, password_shown: true }), 201)));
  const { user } = renderAt(AT);
  await fill(user);
  await user.click(screen.getByRole("button", { name: "Create the account" }));
  expect(await screen.findByText("The temporary password was already shown. If it was lost, make a new one.")).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: /Temporary password/ })).toBeNull();
});

test("an email that already has an account is explained in plain words", async () => {
  signedIn();
  server.use(list([]), http.post("/v1/support/accounts", () => apiError(409, "conflict")));
  const { user } = renderAt(AT);
  await fill(user);
  await user.click(screen.getByRole("button", { name: "Create the account" }));
  expect(await screen.findByText("An account with this email already exists.")).toBeInTheDocument();
  expect(screen.queryByText(/conflict/)).toBeNull();
});

test("an email the sign-in service will not take asks to check it", async () => {
  signedIn();
  server.use(list([]), http.post("/v1/support/accounts", () => apiError(422, "invalid_email")));
  const { user } = renderAt(AT);
  await fill(user);
  await user.click(screen.getByRole("button", { name: "Create the account" }));
  expect(await screen.findByText("Check the email address and try again.")).toBeInTheDocument();
});

test("never asks for a code again: the sign-in code is enough", async () => {
  signedIn();
  let verified = 0;
  server.use(
    list([]),
    http.post("/v1/auth/mfa/verify", () => {
      verified += 1;
      return json({ status: "signed_in", recovery_codes: null });
    }),
    http.post("/v1/support/accounts", () => json(account({ temporary_password: PASSWORD }), 201)),
  );
  const { user } = renderAt(AT);
  await fill(user);
  await user.click(screen.getByRole("button", { name: "Create the account" }));
  await screen.findByRole("region", { name: /Temporary password/ });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(verified).toBe(0);
});

test("an invited account can have a new temporary password; an active one cannot", async () => {
  signedIn();
  let made = 0;
  server.use(
    list([account(), account({ id: "00000000-0000-4000-8000-0000000000a2", email: "old@example.test", status: "active" })]),
    http.post("/v1/support/accounts/:id/temporary-password", ({ request }) => {
      made += request.headers.get("Idempotency-Key") ? 1 : 0;
      return json(account({ last_sent_at: "2026-10-24T10:00:00Z", temporary_password: "WXYZ-abcd-6789-PQRS-tuvw" }));
    }),
  );
  const { user } = renderAt(AT);
  const rows = within(await screen.findByRole("list", { name: "Accounts you've created" })).getAllByRole("listitem");
  expect(rows[1]!).toHaveTextContent("Active");
  expect(within(rows[1]!).queryByRole("button")).toBeNull();
  await user.click(within(rows[0]!).getByRole("button", { name: "Make a new temporary password" }));
  const box = await screen.findByRole("region", { name: "Temporary password for new.expert@example.test" });
  expect(within(box).getByText("WXYZ-abcd-6789-PQRS-tuvw")).toBeInTheDocument();
  expect(screen.getByText("The old temporary password no longer works.")).toBeInTheDocument();
  expect(made).toBe(1);
});

test("a new password too soon asks to wait a minute", async () => {
  signedIn();
  server.use(list([account()]), http.post("/v1/support/accounts/:id/temporary-password", () => apiError(429, "sent_recently")));
  const { user } = renderAt(AT);
  const row = within(await screen.findByRole("list", { name: "Accounts you've created" })).getByRole("listitem");
  await user.click(within(row).getByRole("button", { name: "Make a new temporary password" }));
  expect(await screen.findByText(/wait a minute/i)).toBeInTheDocument();
});

test("the support console links to the page", async () => {
  signedIn();
  renderAt("/support");
  expect(await screen.findByRole("link", { name: "Create an account" })).toHaveAttribute("href", AT);
});

test("has no accessibility problems, with the password shown", async () => {
  signedIn();
  server.use(list([]), http.post("/v1/support/accounts", () => json(account({ temporary_password: PASSWORD }), 201)));
  const { container, user } = renderAt(AT);
  await fill(user);
  await user.click(screen.getByRole("button", { name: "Create the account" }));
  await screen.findByRole("region", { name: /Temporary password/ });
  expect(await axeViolations(container)).toEqual([]);
});
