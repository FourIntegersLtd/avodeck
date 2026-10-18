// Pages opened from emails: choose a new password, and confirm an email change.
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { apiError, http, json, server } from "../../test/api";
import { renderAt } from "../../test/render";

test("choosing a new password from the emailed link signs out everywhere and says so", async () => {
  let sent: unknown;
  server.use(
    http.post("/v1/auth/password/reset", async ({ request }) => {
      sent = await request.json();
      return json({ status: "password_changed" });
    }),
  );
  const { user } = renderAt("/reset-password?token=tok-1");
  expect(await screen.findByRole("heading", { level: 1, name: "Choose a new password" })).toBeInTheDocument();
  await user.type(screen.getByLabelText("New password"), "a brand new password");
  await user.click(screen.getByRole("button", { name: "Save new password" }));
  expect(sent).toEqual({ token: "tok-1", new_password: "a brand new password" });
  expect(await screen.findByText("Your password has been changed. For your safety, we've signed you out everywhere.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/sign-in");
});

test("an expired reset link says what to do next", async () => {
  server.use(http.post("/v1/auth/password/reset", () => apiError(400, "invalid_token")));
  const { user } = renderAt("/reset-password?token=old");
  await user.type(await screen.findByLabelText("New password"), "a brand new password");
  await user.click(screen.getByRole("button", { name: "Save new password" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("That link has expired or was already used. Please ask for a new one.");
});

test("a too-short password is explained", async () => {
  server.use(http.post("/v1/auth/password/reset", () => apiError(422, "invalid", { fields: ["body.new_password"] })));
  const { user } = renderAt("/reset-password?token=tok");
  await user.type(await screen.findByLabelText("New password"), "short");
  await user.click(screen.getByRole("button", { name: "Save new password" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Please use at least 12 characters.");
});

test("confirming an email change from one address asks for the other", async () => {
  server.use(http.post("/v1/me/email-change/confirm", () => json({ status: "waiting_for_other_address" })));
  renderAt("/account/email/confirm?token=t1");
  expect(await screen.findByText("Thank you. Now open the link we sent to your other address.")).toBeInTheDocument();
});

test("confirming from both addresses changes the email", async () => {
  server.use(http.post("/v1/me/email-change/confirm", () => json({ status: "email_changed" })));
  renderAt("/account/email/confirm?token=t2");
  expect(await screen.findByText("Your email address has been changed.")).toBeInTheDocument();
});

test("the sign-in logo goes back to the Avodeck home page", async () => {
  renderAt("/sign-in");
  expect(await screen.findByRole("link", { name: "Avodeck home page" })).toHaveAttribute("href", "http://localhost:3000");
});
