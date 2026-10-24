// Every action tells the person it is working (account screens).
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { http, HttpResponse, json, server, signedIn } from "../../test/api";
import { device, PERSONAL_ORG } from "../../test/fixtures";
import { renderAt } from "../../test/render";

vi.setConfig({ testTimeout: 30_000 });

const OTHER = "77777777-7777-4777-8777-777777777777";

function hold() {
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  return { gate, release };
}

test("signing out one device: the button is busy while held and a second click sends one request", async () => {
  signedIn();
  const { gate, release } = hold();
  let sent = 0;
  server.use(
    http.get("/v1/me/sessions", () => json([device(), device({ id: OTHER, device_label: "Safari on iPad", current: false })])),
    http.get("/v1/me/notification-preferences", () => json([])),
    http.get("/v1/me/close", () => json({ cases: [] })),
    http.delete("/v1/me/sessions/:id", async () => {
      sent += 1;
      await gate;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  const { user } = renderAt("/account");
  const button = await screen.findByRole("button", { name: "Sign out of Safari on iPad" });
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

test("a notification choice shows it is saving while the request is held", async () => {
  signedIn();
  const { gate, release } = hold();
  let sent = 0;
  server.use(
    http.get("/v1/me/sessions", () => json([device()])),
    http.get("/v1/me/close", () => json({ cases: [] })),
    http.get("/v1/me/notification-preferences", () =>
      json([{ organisation_id: PERSONAL_ORG, organisation_name: "Jane Morgan", organisation_kind: "personal", in_app: true, email: true, digest: false, muted_kinds: [] }]),
    ),
    http.put("/v1/me/notification-preferences", async ({ request }) => {
      sent += 1;
      await gate;
      return HttpResponse.json((await request.json()) as never);
    }),
  );
  const { user } = renderAt("/account");
  const section = await screen.findByRole("region", { name: "How we tell you things" });
  const box = await within(section).findByRole("checkbox", { name: "One email a day" });
  try {
    await user.click(box);
    await waitFor(() => expect(box).toBeDisabled());
    expect(within(section).getByRole("group", { name: "My practice" })).toHaveAttribute("aria-busy", "true");
    expect(sent).toBe(1);
  } finally {
    release();
  }
});
