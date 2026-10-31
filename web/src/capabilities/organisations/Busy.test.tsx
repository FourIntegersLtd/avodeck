// Every action tells the person it is working: the button is busy while the request is held,
// and a second click sends one request.
import { screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { http, json, server, signedIn } from "../../test/api";
import { renderAt } from "../../test/render";
import { TEAM_ORG, teamWorld } from "./testing";

vi.setConfig({ testTimeout: 30_000 });

function hold() {
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  return { gate, release };
}

test("Leave: the button is busy while the request is held and a second click sends one request", async () => {
  teamWorld();
  server.use(http.get("/v1/today", () => json({ cases: [], needs_you: [], organisations: [], closed_count: 0 })), http.get("/v1/me/sessions", () => json([])));
  const { gate, release } = hold();
  let sent = 0;
  server.use(
    http.post(`/v1/organisations/${TEAM_ORG}/leave`, async () => {
      sent += 1;
      await gate;
      return new Response(null, { status: 204 });
    }),
  );
  const { user } = renderAt(`/organisations/${TEAM_ORG}/leave`);
  const button = await screen.findByRole("button", { name: "Leave Northgate Medico-Legal" });
  try {
    await user.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    expect(button).toHaveAttribute("aria-busy", "true");
    await user.click(button);
    expect(sent).toBe(1);
  } finally {
    release();
  }
});

test("Custodian form: the Save button is busy while the request is held and a second submit sends one request", async () => {
  signedIn();
  const { gate, release } = hold();
  let sent = 0;
  server.use(
    http.put("/v1/me/custodian", async () => {
      sent += 1;
      await gate;
      return json({ name: "Dr Amira Bello", email: "a.bello@example.test" } as never);
    }),
  );
  const { user } = renderAt("/account/custodian");
  await user.type(await screen.findByLabelText("Their name"), "Dr Amira Bello");
  await user.type(screen.getByLabelText("Their email"), "a.bello@example.test");
  const save = screen.getByRole("button", { name: "Save" });
  try {
    await user.click(save);
    await waitFor(() => expect(save).toBeDisabled());
    expect(save).toHaveAttribute("aria-busy", "true");
    await user.click(save);
    expect(sent).toBe(1);
  } finally {
    release();
  }
});
