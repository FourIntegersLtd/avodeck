// Avodeck support staff (plan phase 1): they land on the support console, not an expert's Today;
// expert-only places send them there; a case they were granted stays reachable. The server decides access.
import { screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";
import { signedIn } from "../test/api";
import { CASE_A, me } from "../test/fixtures";
import { renderAt } from "../test/render";

const staff = () => signedIn({ me: { ...me(), is_support_staff: true } });

for (const path of ["/today", "/profile", "/cases", "/cases/new"]) {
  test(`support staff opening ${path} go to the support console`, async () => {
    staff();
    const { router } = renderAt(path);
    await waitFor(() => expect(router.state.location.pathname).toBe("/support"));
  });
}

test("a case stays reachable for support staff", async () => {
  staff();
  const { router } = renderAt(`/cases/${CASE_A}/read`);
  expect(await screen.findByText("Avodeck support account")).toBeInTheDocument();
  expect(router.state.location.pathname).toBe(`/cases/${CASE_A}/read`);
});

test("an expert opening Today stays on Today", async () => {
  signedIn();
  const { router } = renderAt("/today");
  await screen.findByRole("banner");
  expect(router.state.location.pathname).toBe("/today");
});

