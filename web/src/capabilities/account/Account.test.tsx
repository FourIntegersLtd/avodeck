// S28 Account and security (the foundation part): signing in, backup codes and where you're signed in.
// Risky actions ask for the code again (rule 10) and then carry on.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { apiError, http, HttpResponse, json, server, signedIn } from "../../test/api";
import { device, me } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";

const OTHER = "77777777-7777-4777-8777-777777777777";

function account() {
  signedIn({ me: me({ email: "j.morgan@example.test" }) });
  server.use(
    http.get("/v1/me/sessions", () =>
      json([
        device(),
        device({ id: OTHER, device_label: "Safari on iPad", current: false, last_seen_at: new Date(Date.now() - 2 * 86_400_000).toISOString() }),
      ]),
    ),
  );
}

test("shows email, the code, backup codes and devices, and passes axe", async () => {
  account();
  const { container } = renderAt("/account");
  expect(await screen.findByRole("heading", { level: 1, name: "Account and security" })).toBeInTheDocument();
  expect(await screen.findByText("j.morgan@example.test")).toBeInTheDocument();
  expect(screen.getByText("6-digit code from your phone")).toBeInTheDocument();
  expect(screen.getByText("Always on. It keeps the records safe.")).toBeInTheDocument();
  const devices = await screen.findByRole("region", { name: "Where you're signed in" });
  expect(within(devices).getByText("Chrome on Windows")).toBeInTheDocument();
  expect(within(devices).getByText("This device · now")).toBeInTheDocument();
  expect(within(devices).getByText("2 days ago")).toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
});

test("signing out one other device ends that session", async () => {
  account();
  let deleted = "";
  server.use(
    http.delete("/v1/me/sessions/:id", ({ params }) => {
      deleted = String(params.id);
      return new HttpResponse(null, { status: 204 });
    }),
  );
  const { user } = renderAt("/account");
  await user.click(await screen.findByRole("button", { name: "Sign out of Safari on iPad" }));
  await waitFor(() => expect(deleted).toBe(OTHER));
});

test("sign out everywhere else asks for the code first, then carries on", async () => {
  account();
  let revoked = 0;
  let stepped = false;
  server.use(
    http.post("/v1/me/sessions/revoke-all", () => {
      if (!stepped) return apiError(403, "step_up_required");
      revoked += 1;
      return new HttpResponse(null, { status: 204 });
    }),
    http.post("/v1/auth/mfa/verify", async ({ request }) => {
      expect(await request.json()).toEqual({ code: "654321" });
      stepped = true;
      return json({ status: "signed_in", recovery_codes: null });
    }),
  );
  const { user } = renderAt("/account");
  await user.click(await screen.findByRole("button", { name: "Sign out everywhere else" }));
  const dialog = await screen.findByRole("dialog", { name: "Please enter your code" });
  await user.type(within(dialog).getByLabelText("6-digit code"), "654321");
  await user.click(within(dialog).getByRole("button", { name: "Carry on" }));
  await waitFor(() => expect(revoked).toBe(1));
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("cancelling the code does nothing", async () => {
  account();
  server.use(http.post("/v1/me/sessions/revoke-all", () => apiError(403, "step_up_required")));
  const { user } = renderAt("/account");
  await user.click(await screen.findByRole("button", { name: "Sign out everywhere else" }));
  const dialog = await screen.findByRole("dialog", { name: "Please enter your code" });
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("making new backup codes shows them once", async () => {
  account();
  server.use(http.post("/v1/auth/mfa/recovery-codes", () => json({ recovery_codes: ["1111-2222", "3333-4444"] })));
  const { user } = renderAt("/account");
  await user.click(await screen.findByRole("button", { name: "Make new codes" }));
  expect(await screen.findByText("1111-2222")).toBeInTheDocument();
  expect(screen.getByText("Your old backup codes no longer work. Keep these somewhere safe, away from your phone.")).toBeInTheDocument();
});

test("changing email sends a link to both addresses", async () => {
  account();
  let sent: unknown;
  server.use(
    http.post("/v1/me/email-change", async ({ request }) => {
      sent = await request.json();
      return json({ status: "confirmation_sent" }, 202);
    }),
  );
  const { user } = renderAt("/account");
  await user.click(await screen.findByRole("button", { name: "Change email" }));
  await user.type(screen.getByLabelText("New email address"), "jane@example.test");
  await user.click(screen.getByRole("button", { name: "Send the links" }));
  expect(sent).toEqual({ new_email: "jane@example.test" });
  expect(await screen.findByText("We've sent a link to your old and new addresses. Open both links to finish.")).toBeInTheDocument();
});

test("changing password sends a link to choose a new one", async () => {
  account();
  let sent: unknown;
  server.use(
    http.post("/v1/auth/password/forgot", async ({ request }) => {
      sent = await request.json();
      return json({ status: "sent" }, 202);
    }),
  );
  const { user } = renderAt("/account");
  await user.click(await screen.findByRole("button", { name: "Change password" }));
  expect(sent).toEqual({ email: "j.morgan@example.test" });
  expect(await screen.findByText("We've sent you a link to choose a new password.")).toBeInTheDocument();
});

test("sign out ends this session and goes back to sign in", async () => {
  account();
  server.use(
    http.post("/v1/auth/sign-out", () => {
      server.use(http.get("/v1/auth/session", () => apiError(401, "not_signed_in")));
      return new HttpResponse(null, { status: 204 });
    }),
  );
  const { user, router } = renderAt("/account");
  await user.click(await screen.findByRole("button", { name: "Sign out" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/sign-in"));
});

test("backup codes say how many are left, and the count follows making new ones", async () => {
  account();
  let left = 7;
  server.use(
    http.get("/v1/auth/mfa/recovery-codes", () => json({ remaining: left, total: 10 })),
    http.post("/v1/auth/mfa/recovery-codes", () => {
      left = 10;
      return json({ recovery_codes: ["AAAAA-BBBBB"] });
    }),
  );
  const { user } = renderAt("/account");
  expect(await screen.findByText(/7 of 10 left\./)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Make new codes" }));
  expect(await screen.findByText(/10 of 10 left\./)).toBeInTheDocument();
});

test("the board has a way back to the profile", async () => {
  account();
  renderAt("/account");
  await screen.findByRole("heading", { level: 1, name: "Account and security" });
  const back = document.querySelector("a.av-back-link");
  expect(back).toHaveTextContent("Your profile");
});

test("the email form can be cancelled", async () => {
  account();
  const { user } = renderAt("/account");
  await user.click(await screen.findByRole("button", { name: "Change email" }));
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByLabelText("New email address")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Change email" })).toBeInTheDocument();
});
