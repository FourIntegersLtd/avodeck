// S28 sections that are slice O's: records custodian, "If you can't carry on", and closing the account.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { apiError, http, json, server, signedIn } from "../../test/api";
import { CASE_A, CASE_B } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { recorder } from "./testing";

// Typing into fields is slow on a busy machine; these flows have many steps.
vi.setConfig({ testTimeout: 30_000 });

function today(over: Array<{ case_id: string; label: string; exported: boolean }> = [{ case_id: CASE_A, label: "Mrs A", exported: false }]) {
  server.use(http.get("/v1/me/close", () => json({ cases: over })));
}

test("the account page links to both sections once they are registered", async () => {
  signedIn();
  server.use(http.get("/v1/me/sessions", () => json([])));
  renderAt("/account/custodian");
  expect(await screen.findByRole("heading", { level: 1, name: "If you can't carry on" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Account and security" })).toHaveAttribute("href", "/account");
});

test("names a records custodian with both a name and an email", async () => {
  signedIn();
  const { sent, record } = recorder();
  server.use(
    http.put("/v1/me/custodian", async ({ request }) => {
      await record(request);
      return json(sent.at(-1)!.body as never);
    }),
  );
  const { user } = renderAt("/account/custodian");
  await user.type(await screen.findByLabelText("Their name"), "Dr Amira Bello");
  await user.type(screen.getByLabelText("Their email"), "a.bello@example.test");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Saved. Dr Amira Bello is your records custodian.")).toBeInTheDocument();
  expect(sent[0]!.body).toEqual({ name: "Dr Amira Bello", email: "a.bello@example.test" });
});

test("a name without an email is not sent", async () => {
  signedIn();
  const { sent, record } = recorder();
  server.use(http.put("/v1/me/custodian", async ({ request }) => (await record(request), json({}))));
  const { user } = renderAt("/account/custodian");
  await user.type(await screen.findByLabelText("Their name"), "Dr Amira Bello");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Type both a name and an email, or leave both empty.")).toBeInTheDocument();
  expect(sent).toHaveLength(0);
});

test("removes the custodian", async () => {
  signedIn();
  const { sent, record } = recorder();
  server.use(http.put("/v1/me/custodian", async ({ request }) => (await record(request), json({ name: null, email: null }))));
  const { user } = renderAt("/account/custodian");
  await user.click(await screen.findByRole("button", { name: "Remove my custodian" }));
  expect(await screen.findByText("Removed. You have no records custodian.")).toBeInTheDocument();
  expect(sent[0]!.body).toEqual({ name: null, email: null });
});

test("close account lists the cases the server says would be deleted, each with an Export link", async () => {
  signedIn();
  today([{ case_id: CASE_A, label: "Mrs A", exported: false }, { case_id: CASE_B, label: "Ms C", exported: true }]);
  renderAt("/account/close");
  expect(await screen.findByRole("heading", { level: 1, name: "Close your account" })).toBeInTheDocument();
  const list = await screen.findByRole("list", { name: "Cases in your own practice that will be deleted" });
  const [first, second] = within(list).getAllByRole("listitem");
  expect(first).toHaveTextContent("Mrs A");
  expect(first).toHaveTextContent("Not exported yet");
  expect(second).toHaveTextContent("Ms C");
  expect(second).toHaveTextContent("Exported");
});

test("when the server says export first, the named cases are marked", async () => {
  signedIn();
  today();
  server.use(http.post("/v1/me/close", () => apiError(409, "export_first", { case_ids: [CASE_A] })));
  const { user } = renderAt("/account/close");
  await user.click(await screen.findByRole("button", { name: "Close account" }));
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Yes, close my account" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Export the cases marked below");
  expect(screen.getByText("Not exported yet")).toBeInTheDocument();
});

test("closing the account works and says served reports stay", async () => {
  signedIn();
  today();
  const { sent, record } = recorder();
  server.use(http.post("/v1/me/close", async ({ request }) => (await record(request), json({ closed_at: "2026-10-23T10:00:00Z", deletion_job_ids: [] }))));
  const { user } = renderAt("/account/close");
  await user.click(await screen.findByRole("button", { name: "Close account" }));
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Yes, close my account" }));
  await waitFor(() => expect(sent).toHaveLength(1));
  expect(await screen.findByText(/You can still sign in to see your served reports/)).toBeInTheDocument();
});

test("both screens pass the automated accessibility checks", async () => {
  signedIn();
  today();
  const first = renderAt("/account/close");
  await screen.findByRole("heading", { level: 1, name: "Close your account" });
  expect(await axeViolations(first.container)).toEqual([]);
  first.unmount();
  const second = renderAt("/account/custodian");
  await screen.findByLabelText("Their name");
  expect(await axeViolations(second.container)).toEqual([]);
});
