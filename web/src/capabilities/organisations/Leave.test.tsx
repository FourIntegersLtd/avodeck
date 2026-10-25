// Leave an organisation: confirm screen that lists the named cases the person keeps.
import { screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { org, TEAM_ORG, teamWorld } from "./testing";

const AT = `/organisations/${TEAM_ORG}/leave`;

function cases(blocking = [{ case_id: CASE_A, label: "Mrs A" }]) {
  server.use(http.get(`/v1/organisations/${TEAM_ORG}`, () => json(org({ blocking_cases: blocking }))));
}

test("lists the cases the person keeps as named expert, and only those", async () => {
  teamWorld();
  cases();
  renderAt(AT);
  expect(await screen.findByRole("heading", { level: 1, name: "Leave Northgate Medico-Legal?" })).toBeInTheDocument();
  expect(await screen.findByText("Mrs A")).toBeInTheDocument();
  expect(screen.queryByText("Ms C")).toBeNull();
});

test("says so when they keep nothing", async () => {
  teamWorld();
  cases([]);
  renderAt(AT);
  expect(await screen.findByText(/not the named expert on any case here/)).toBeInTheDocument();
});

test("leaving sends the request and goes back to the account", async () => {
  teamWorld();
  cases();
  let left = 0;
  server.use(
    http.post(`/v1/organisations/${TEAM_ORG}/leave`, () => {
      left += 1;
      return new Response(null, { status: 204 });
    }),
    http.get("/v1/me/sessions", () => json([])),
  );
  const { user, router } = renderAt(AT);
  await user.click(await screen.findByRole("button", { name: "Leave Northgate Medico-Legal" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/account"));
  expect(left).toBe(1);
});

test("the last owner is told why they cannot leave", async () => {
  teamWorld();
  cases();
  server.use(http.post(`/v1/organisations/${TEAM_ORG}/leave`, () => apiError(409, "last_owner")));
  const { user } = renderAt(AT);
  await user.click(await screen.findByRole("button", { name: "Leave Northgate Medico-Legal" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Every team keeps at least one owner.");
});

test("passes the automated accessibility checks", async () => {
  teamWorld();
  cases();
  const { container } = renderAt(AT);
  await screen.findByText("Mrs A");
  expect(await axeViolations(container)).toEqual([]);
});
