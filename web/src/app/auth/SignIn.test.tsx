// S01 Sign in: password, then the 6-digit code; first sign-in sets up the code and shows the
// backup codes once; a lost phone uses a backup code; a forgotten password sends a link.
import { screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";
import { apiError, http, json, server, signedIn } from "../../test/api";
import { axeViolations, renderAt } from "../../test/render";

function signedOutAtFirst() {
  server.use(http.get("/v1/auth/session", () => apiError(401, "not_signed_in")));
}

async function enterPassword(user: ReturnType<typeof renderAt>["user"]) {
  await user.type(await screen.findByLabelText("Email address"), "j.morgan@example.test");
  await user.type(screen.getByLabelText("Password"), "a long enough password");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

test("the sign-in screen matches its board and passes axe", async () => {
  signedOutAtFirst();
  const { container } = renderAt("/sign-in");
  expect(await screen.findByRole("heading", { level: 1, name: "Welcome back" })).toBeInTheDocument();
  expect(screen.getByText("Sign in to carry on where you left off.")).toBeInTheDocument();
  expect(screen.getByText(/Every page read\. Every fact traced\./)).toBeInTheDocument();
  expect(screen.getByText("Your opinion is always your own. We never write it.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Forgotten your password?" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Use a backup code" })).toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
});

test("show password reveals what was typed", async () => {
  signedOutAtFirst();
  const { user } = renderAt("/sign-in");
  const password = await screen.findByLabelText("Password");
  expect(password).toHaveAttribute("type", "password");
  await user.click(screen.getByLabelText("Show password"));
  expect(password).toHaveAttribute("type", "text");
});

test("password then code signs in and opens Today", async () => {
  signedOutAtFirst();
  let sent: unknown;
  server.use(
    http.post("/v1/auth/sign-in", async ({ request }) => {
      sent = await request.json();
      return json({ status: "mfa_required", mfa_enrolled: true });
    }),
    http.post("/v1/auth/mfa/verify", () => {
      signedIn();
      return json({ status: "signed_in", recovery_codes: null });
    }),
  );
  const { user, router } = renderAt("/sign-in");
  await enterPassword(user);
  expect(sent).toEqual({ email: "j.morgan@example.test", password: "a long enough password" });
  expect(await screen.findByRole("heading", { level: 1, name: "Enter your code" })).toBeInTheDocument();
  await user.type(screen.getByLabelText("6-digit code"), "123456");
  await user.click(screen.getByRole("button", { name: "Carry on" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/today"));
});

test("a wrong password says so plainly, with no technical code", async () => {
  signedOutAtFirst();
  server.use(http.post("/v1/auth/sign-in", () => apiError(401, "invalid_credentials")));
  const { user } = renderAt("/sign-in");
  await enterPassword(user);
  expect(await screen.findByRole("alert")).toHaveTextContent("That email and password don't match. Please try again.");
  expect(screen.queryByText(/invalid_credentials|401/)).toBeNull();
});

test("a wrong code says so and lets the expert try again", async () => {
  signedOutAtFirst();
  server.use(
    http.post("/v1/auth/sign-in", () => json({ status: "mfa_required", mfa_enrolled: true })),
    http.post("/v1/auth/mfa/verify", () => apiError(400, "invalid_code")),
  );
  const { user } = renderAt("/sign-in");
  await enterPassword(user);
  await user.type(await screen.findByLabelText("6-digit code"), "000000");
  await user.click(screen.getByRole("button", { name: "Carry on" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("That code didn't work. Please check your phone and try again.");
  expect(screen.getByLabelText("6-digit code")).toBeInTheDocument();
});

test("first sign-in sets up the code, then shows the backup codes once", async () => {
  signedOutAtFirst();
  server.use(
    http.post("/v1/auth/sign-in", () => json({ status: "mfa_required", mfa_enrolled: false })),
    http.post("/v1/auth/mfa/enroll", () =>
      json({ qr_code: "data:image/svg+xml;utf8,<svg/>", secret: "JBSWY3DPEHPK3PXP", uri: "otpauth://totp/x" }),
    ),
    http.post("/v1/auth/mfa/verify", () => {
      signedIn();
      return json({ status: "signed_in", recovery_codes: ["aaaa-bbbb", "cccc-dddd"] });
    }),
  );
  const { user, router, container } = renderAt("/sign-in");
  await enterPassword(user);
  expect(await screen.findByRole("heading", { level: 1, name: "Set up the code on your phone" })).toBeInTheDocument();
  expect(screen.getByRole("img", { name: "Picture to scan with your authenticator app" })).toBeInTheDocument();
  expect(screen.getByText("JBSWY3DPEHPK3PXP")).toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
  await user.type(screen.getByLabelText("6-digit code"), "123456");
  await user.click(screen.getByRole("button", { name: "Carry on" }));
  expect(await screen.findByRole("heading", { level: 1, name: "Your backup codes" })).toBeInTheDocument();
  expect(screen.getByText("aaaa-bbbb")).toBeInTheDocument();
  expect(screen.getByText("cccc-dddd")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "I've saved my codes" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/today"));
});

test("a lost phone: email, password and a backup code, then set up the new phone", async () => {
  signedOutAtFirst();
  let sent: unknown;
  server.use(
    http.post("/v1/auth/mfa/recover", async ({ request }) => {
      sent = await request.json();
      return json({ status: "mfa_required", mfa_enrolled: false });
    }),
    http.post("/v1/auth/mfa/enroll", () => json({ qr_code: "data:image/svg+xml;utf8,<svg/>", secret: "SECRET", uri: "otpauth://x" })),
  );
  const { user } = renderAt("/sign-in");
  await user.click(await screen.findByRole("button", { name: "Use a backup code" }));
  expect(screen.getByRole("heading", { level: 1, name: "Use a backup code" })).toBeInTheDocument();
  await user.type(screen.getByLabelText("Email address"), "j.morgan@example.test");
  await user.type(screen.getByLabelText("Password"), "a long enough password");
  await user.type(screen.getByLabelText("Backup code"), "aaaa-bbbb");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  expect(sent).toEqual({ email: "j.morgan@example.test", password: "a long enough password", code: "aaaa-bbbb" });
  expect(await screen.findByRole("heading", { level: 1, name: "Set up the code on your phone" })).toBeInTheDocument();
});

test("forgotten password sends a link and says the same thing whatever the address", async () => {
  signedOutAtFirst();
  let sent: unknown;
  server.use(
    http.post("/v1/auth/password/forgot", async ({ request }) => {
      sent = await request.json();
      return json({ status: "sent" }, 202);
    }),
  );
  const { user } = renderAt("/sign-in");
  await user.click(await screen.findByRole("button", { name: "Forgotten your password?" }));
  await user.type(screen.getByLabelText("Email address"), "someone@example.test");
  await user.click(screen.getByRole("button", { name: "Send the link" }));
  expect(sent).toEqual({ email: "someone@example.test" });
  expect(await screen.findByText("If that address has an Avodeck account, we've sent a link to it. The link works for one hour.")).toBeInTheDocument();
});

test("a person who signed in but has not entered a code is taken to the code step", async () => {
  server.use(http.get("/v1/auth/session", () => json({ aal: "aal1", mfa_enrolled: true, mfa_complete: false, step_up_until: null, minutes_until_timeout: 30 })));
  const { router } = renderAt("/today");
  expect(await screen.findByRole("heading", { level: 1, name: "Enter your code" })).toBeInTheDocument();
  expect(router.state.location.pathname).toBe("/sign-in");
});

test("a signed-out person who opens Today is sent to sign in", async () => {
  signedOutAtFirst();
  const { router } = renderAt("/today");
  expect(await screen.findByRole("heading", { level: 1, name: "Welcome back" })).toBeInTheDocument();
  expect(router.state.location.pathname).toBe("/sign-in");
});
