// A capability can add a section to S28 without the foundation knowing it (dummy registration).
import { screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { registerAccountSection, resetSlots } from "../../foundation/lib/slots";
import { http, json, server, signedIn } from "../../test/api";
import { me } from "../../test/fixtures";
import { renderAt } from "../../test/render";

afterEach(() => resetSlots());

function setup() {
  signedIn({ me: me() });
  server.use(http.get("/v1/me/sessions", () => json([])));
}

test("registered sections render in order after the foundation's, each under its own heading", async () => {
  setup();
  registerAccountSection({ key: "second", order: 20, titleKey: "account.notify.in_app", component: () => <p>second body</p> });
  registerAccountSection({ key: "first", order: 10, titleKey: "account.notify.email", component: () => <p>first body</p> });
  renderAt("/account");
  const first = await screen.findByRole("region", { name: "By email" });
  const second = screen.getByRole("region", { name: "In the app" });
  expect(first).toHaveTextContent("first body");
  expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
});

test("with nothing registered the page is unchanged", async () => {
  setup();
  renderAt("/account");
  await screen.findByRole("heading", { level: 1 });
  expect(screen.queryByText("first body")).toBeNull();
});
