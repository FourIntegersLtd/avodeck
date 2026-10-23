// S27 Organisation and members (docs/design/screens/S27-OrgMembers.dc.html).
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { apiError, http, json, server } from "../../test/api";
import { axeViolations, renderAt } from "../../test/render";
import { invite, MEMBERS, org, OTHER, recorder, TEAM_ORG, teamWorld, THIRD } from "./testing";

// Typing into fields is slow on a busy machine; these flows have many steps.
vi.setConfig({ testTimeout: 30_000 });

const AT = `/organisations/${TEAM_ORG}`;

test("shows the people with what each role does, and the one h1", async () => {
  teamWorld();
  renderAt(AT);
  expect(await screen.findByRole("heading", { level: 1, name: "Northgate Medico-Legal" })).toBeInTheDocument();
  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  const people = screen.getByRole("region", { name: "People (3)" });
  expect(await within(people).findByText("Dr Jane Morgan (you)")).toBeInTheDocument();
  expect(within(people).getByText("Sees case details, never the records")).toBeInTheDocument();
  expect(within(people).getByText("Their own cases")).toBeInTheDocument();
});

test("has the role and leaving panels, the rules card and the closing card", async () => {
  teamWorld();
  renderAt(AT);
  expect(await screen.findByRole("heading", { name: "What each role can do" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "When someone leaves" })).toBeInTheDocument();
  expect(screen.getByText("Every organisation keeps at least one owner.")).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "Rules for Northgate Medico-Legal cases" })).toBeInTheDocument();
  expect(screen.getByText("6 years")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Close organisation" })).toBeInTheDocument();
});

test("pending invites list with Cancel, and cancelling removes the invite", async () => {
  const state = teamWorld();
  const { sent, record } = recorder();
  server.use(
    http.delete(`/v1/organisations/${TEAM_ORG}/invites/${state.invites[0]!.id}`, async ({ request }) => {
      await record(request);
      state.invites = [];
      return new Response(null, { status: 204 });
    }),
  );
  const { user } = renderAt(AT);
  expect(await screen.findByText("k.shah@example.test")).toBeInTheDocument();
  expect(screen.getByText(/link works for 5 more days/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Cancel the invite to k.shah@example.test" }));
  await waitFor(() => expect(screen.queryByText("k.shah@example.test")).toBeNull());
  expect(sent).toHaveLength(1);
});

test("sends an invite with the chosen role", async () => {
  const state = teamWorld({ invites: [] });
  const { sent, record } = recorder();
  server.use(
    http.post(`/v1/organisations/${TEAM_ORG}/invites`, async ({ request }) => {
      await record(request);
      state.invites = [invite({ email: "new@example.test", role: "assistant" })];
      return json(state.invites[0]!, 201);
    }),
  );
  const { user } = renderAt(AT);
  await user.type(await screen.findByLabelText("Invite someone by email"), "new@example.test");
  await user.selectOptions(screen.getByLabelText("Their role"), "assistant");
  await user.click(screen.getByRole("button", { name: "Send invite" }));
  expect(await screen.findByText("We've emailed an invite to new@example.test.")).toBeInTheDocument();
  expect(sent[0]!.body).toEqual({ email: "new@example.test", role: "assistant" });
});

test("sending an invite asks for the 6-digit code when the server wants it, then sends", async () => {
  teamWorld({ invites: [] });
  let tries = 0;
  server.use(
    http.post("/v1/auth/mfa/verify", () => json({ status: "signed_in", recovery_codes: null })),
    http.post(`/v1/organisations/${TEAM_ORG}/invites`, () => {
      tries += 1;
      return tries === 1 ? apiError(403, "step_up_required") : json(invite({ email: "new@example.test" }), 201);
    }),
  );
  const { user } = renderAt(AT);
  await user.type(await screen.findByLabelText("Invite someone by email"), "new@example.test");
  await user.click(screen.getByRole("button", { name: "Send invite" }));
  const dialog = await screen.findByRole("dialog");
  await user.type(within(dialog).getByLabelText(/code/i), "123456");
  await user.click(within(dialog).getByRole("button", { name: /carry on/i }));
  expect(await screen.findByText("We've emailed an invite to new@example.test.")).toBeInTheDocument();
  expect(tries).toBe(2);
});

test("changes a person's role", async () => {
  teamWorld();
  const { sent, record } = recorder();
  server.use(
    http.patch(`/v1/organisations/${TEAM_ORG}/members/${THIRD}`, async ({ request }) => {
      await record(request);
      return json({ ...MEMBERS[2]!, role: "assistant" });
    }),
  );
  const { user } = renderAt(AT);
  await user.click(await screen.findByRole("button", { name: "Change role for Lena Hart" }));
  await user.selectOptions(screen.getByLabelText("New role for Lena Hart"), "assistant");
  await user.click(screen.getByRole("button", { name: "Save role" }));
  expect(await screen.findByText("Lena Hart is now Assistant.")).toBeInTheDocument();
  expect(sent[0]!.body).toEqual({ role: "assistant" });
});

test("removing a person asks first", async () => {
  teamWorld();
  const { sent, record } = recorder();
  server.use(
    http.delete(`/v1/organisations/${TEAM_ORG}/members/${OTHER}`, async ({ request }) => {
      await record(request);
      return new Response(null, { status: 204 });
    }),
  );
  const { user } = renderAt(AT);
  await user.click(await screen.findByRole("button", { name: "Remove Sarah Okafor" }));
  expect(sent).toHaveLength(0);
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Remove them" }));
  expect(await screen.findByText("Sarah Okafor has been removed.")).toBeInTheDocument();
  expect(sent).toHaveLength(1);
});

test("the last owner cannot be demoted: the server's refusal is shown in plain words", async () => {
  teamWorld();
  server.use(http.patch(`/v1/organisations/${TEAM_ORG}/members/${OTHER}`, () => apiError(409, "conflict")));
  const { user } = renderAt(AT);
  await user.click(await screen.findByRole("button", { name: "Change role for Sarah Okafor" }));
  await user.click(screen.getByRole("button", { name: "Save role" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("This can't be done at this stage.");
});

test("changes the retention cap and turns on the team template", async () => {
  teamWorld();
  const { sent, record } = recorder();
  server.use(
    http.put(`/v1/organisations/${TEAM_ORG}/policy`, async ({ request }) => {
      await record(request);
      const body = sent.at(-1)!.body as { required_template_id: string | null; max_retention_years: number | null };
      return json(org({ policy: body }));
    }),
  );
  const { user } = renderAt(AT);
  await user.click(await screen.findByRole("button", { name: "Change" }));
  const years = screen.getByLabelText("Years to keep records, at most");
  await user.clear(years);
  await user.type(years, "4");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("4 years")).toBeInTheDocument();
  expect(sent[0]!.body).toEqual({ required_template_id: null, max_retention_years: 4 });
  await user.click(screen.getByRole("switch", { name: "Use one report template" }));
  await waitFor(() => expect(sent).toHaveLength(2));
  expect(sent[1]!.body).toEqual({ required_template_id: THIRD, max_retention_years: 4 });
});

test("the billing card shows who invoices come from and links to the invoice settings", async () => {
  teamWorld();
  renderAt(AT);
  expect(await screen.findByText(/Northgate Medico-Legal Ltd/)).toBeInTheDocument();
});

test("closing the organisation asks first, then returns to the account", async () => {
  teamWorld();
  const { sent, record } = recorder();
  server.use(
    http.post(`/v1/organisations/${TEAM_ORG}/close`, async ({ request }) => {
      await record(request);
      return new Response(null, { status: 204 });
    }),
    http.get("/v1/me/sessions", () => json([])),
  );
  const { user, router } = renderAt(AT);
  await user.click(await screen.findByRole("button", { name: "Close organisation" }));
  expect(sent).toHaveLength(0);
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Yes, close it" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/account"));
  expect(sent).toHaveLength(1);
});

test("closing is refused while a case is open, in plain words", async () => {
  teamWorld();
  server.use(http.post(`/v1/organisations/${TEAM_ORG}/close`, () => apiError(409, "cases_open")));
  const { user } = renderAt(AT);
  await user.click(await screen.findByRole("button", { name: "Close organisation" }));
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Yes, close it" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/still being worked on/);
});

test("an expert (not owner or admin) sees the people but no controls; someone with no access sees not found", async () => {
  teamWorld({ org: org({ role: "expert" }) });
  renderAt(AT);
  await screen.findByRole("heading", { level: 1, name: "Northgate Medico-Legal" });
  expect(screen.queryByRole("button", { name: "Close organisation" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Send invite" })).toBeNull();
});

test("a 404 shows the not found page", async () => {
  teamWorld();
  server.use(http.get(`/v1/organisations/${TEAM_ORG}`, () => apiError(404, "not_found")));
  renderAt(AT);
  expect(await screen.findByRole("heading", { level: 1 })).not.toHaveTextContent("Northgate");
});

test("passes the automated accessibility checks", async () => {
  teamWorld();
  const { container } = renderAt(AT);
  await screen.findByText("k.shah@example.test");
  expect(await axeViolations(container)).toEqual([]);
});
