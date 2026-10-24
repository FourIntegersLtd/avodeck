// S01: Avodeck support staff land on the support console after signing in, not on Today.
import { screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";
import { apiError, http, json, server, signedIn } from "../../test/api";
import { me } from "../../test/fixtures";
import { renderAt } from "../../test/render";

test("support staff signing in land on the support console", async () => {
  server.use(
    http.get("/v1/auth/session", () => apiError(401, "not_signed_in")),
    http.post("/v1/auth/sign-in", () => {
      signedIn({ me: { ...me(), is_support_staff: true } });
      return json({ status: "signed_in", mfa_enrolled: true });
    }),
  );
  const { user, router } = renderAt("/sign-in");
  await user.type(await screen.findByLabelText("Email address"), "staff@example.test");
  await user.type(screen.getByLabelText("Password"), "a long enough password");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/support"));
});
