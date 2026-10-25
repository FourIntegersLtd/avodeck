// Support creates an account for a new person at /support/accounts: a name and an email, nothing else (a
// team's own owners and admins invite people to a team).
// The person gets an email to set their own password; support never sees or chooses it.
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
  expect(screen.getByText(/set their own password/i)).toBeInTheDocument();
  expect(screen.queryByLabelText(/team/i)).toBeNull();
  expect(screen.queryByLabelText(/role/i)).toBeNull();
});

test("says plainly the link lasts 1 hour and can be sent again", async () => {
  signedIn();
  server.use(list([]));
  renderAt(AT);
  await screen.findByRole("heading", { level: 1, name: "Create an account" });
  expect(screen.getByText(/The link lasts 1 hour\. If it runs out, send the invite again\./)).toBeInTheDocument();
});

test("creates an account with an idempotency key and shows it in the list as invited", async () => {
  signedIn();
  let sent: { body: unknown; key: string | null } | null = null;
  server.use(
    list([]),
    http.post("/v1/support/accounts", async ({ request }) => {
      sent = { body: await request.json(), key: request.headers.get("Idempotency-Key") };
      return json(account(), 201);
    }),
  );
  const { user } = renderAt(AT);
  await fill(user);
  await user.click(screen.getByRole("button", { name: "Create the account" }));
  expect(await screen.findByText("We've emailed new.expert@example.test a link to set their password. It lasts 1 hour.")).toBeInTheDocument();
  expect(sent!.body).toEqual({ full_name: "Dr New Expert", email: "new.expert@example.test" });
  expect(sent!.key).toBeTruthy();
  const row = within(screen.getByRole("list", { name: "Accounts you've created" })).getByRole("listitem");
  expect(row).toHaveTextContent("new.expert@example.test");
  expect(row).toHaveTextContent("Invited");
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

test("asks for the code first when the last check was too long ago", async () => {
  signedIn();
  let tries = 0;
  server.use(
    list([]),
    http.post("/v1/auth/mfa/verify", () => json({ status: "signed_in", recovery_codes: null })),
    http.post("/v1/support/accounts", () => {
      tries += 1;
      return tries === 1 ? apiError(403, "step_up_required") : json(account(), 201);
    }),
  );
  const { user } = renderAt(AT);
  await fill(user);
  await user.click(screen.getByRole("button", { name: "Create the account" }));
  const dialog = await screen.findByRole("dialog");
  await user.type(within(dialog).getByLabelText(/code/i), "123456");
  await user.click(within(dialog).getByRole("button", { name: /carry on/i }));
  expect(await screen.findByText(/We've emailed/)).toBeInTheDocument();
  expect(tries).toBe(2);
});

test("an invited account can be sent the invite again; an active one cannot", async () => {
  signedIn();
  let resent = 0;
  server.use(
    list([account(), account({ id: "00000000-0000-4000-8000-0000000000a2", email: "old@example.test", status: "active" })]),
    http.post("/v1/support/accounts/:id/resend", ({ request }) => {
      resent += request.headers.get("Idempotency-Key") ? 1 : 0;
      return json(account({ last_sent_at: "2026-10-24T10:00:00Z" }));
    }),
  );
  const { user } = renderAt(AT);
  const rows = within(await screen.findByRole("list", { name: "Accounts you've created" })).getAllByRole("listitem");
  expect(rows[1]!).toHaveTextContent("Active");
  expect(within(rows[1]!).queryByRole("button")).toBeNull();
  await user.click(within(rows[0]!).getByRole("button", { name: "Send the invite again" }));
  expect(await screen.findByText("We've sent the invite to new.expert@example.test again. The new link lasts 1 hour.")).toBeInTheDocument();
  expect(resent).toBe(1);
});

test("sending again too soon asks to wait a minute", async () => {
  signedIn();
  server.use(list([account()]), http.post("/v1/support/accounts/:id/resend", () => apiError(429, "sent_recently")));
  const { user } = renderAt(AT);
  const row = within(await screen.findByRole("list", { name: "Accounts you've created" })).getByRole("listitem");
  await user.click(within(row).getByRole("button", { name: "Send the invite again" }));
  expect(await screen.findByText(/wait a minute/i)).toBeInTheDocument();
});

test("the support console links to the page", async () => {
  signedIn();
  renderAt("/support");
  expect(await screen.findByRole("link", { name: "Create an account" })).toHaveAttribute("href", AT);
});

test("has no accessibility problems", async () => {
  signedIn();
  server.use(list([account()]));
  const { container } = renderAt(AT);
  await screen.findByRole("list", { name: "Accounts you've created" });
  expect(await axeViolations(container)).toEqual([]);
});
