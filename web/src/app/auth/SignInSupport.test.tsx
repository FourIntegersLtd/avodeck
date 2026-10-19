// S01: "Need help? Call us on …" under the form, from GET /v1/cell; no line when the cell gives no number.
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, json, server, signedOut } from "../../test/api";
import { renderAt } from "../../test/render";

test("shows the support number and hours from the cell", async () => {
  signedOut();
  server.use(http.get("/v1/cell", () => json({ storage_place: null, support_phone: "020 7946 0000", support_hours: "weekdays 8am to 6pm" })));
  renderAt("/sign-in");
  expect(await screen.findByTestId("support-line")).toHaveTextContent("Need help? Call us on 020 7946 0000, weekdays 8am to 6pm.");
  expect(screen.getByRole("link", { name: "020 7946 0000" })).toHaveAttribute("href", "tel:02079460000");
});

test("hides the line when the cell has no number", async () => {
  signedOut();
  renderAt("/sign-in");
  await screen.findByRole("note");
  expect(screen.queryByText(/Need help/)).toBeNull();
});

test("leaves out the hours when the cell gives only a number", async () => {
  signedOut();
  server.use(http.get("/v1/cell", () => json({ storage_place: null, support_phone: "020 7946 0000", support_hours: null })));
  renderAt("/sign-in");
  expect(await screen.findByTestId("support-line")).toHaveTextContent(/^Need help\? Call us on 020 7946 0000\.$/);
});
