// S03 against its canvas board: the balance footer, and Cases and Help in the header.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, json, server, signedIn } from "../../test/api";
import { launchPack } from "../../test/fixtures";
import { renderAt } from "../../test/render";

const word = (key: string) => launchPack.party_roles.find((r) => r.key === key)!.label;

test("the footer counts this year's cases by side, in the pack's words", async () => {
  signedIn();
  server.use(http.get("/v1/me/stats/balance", () => json({ counts: { other_side: 14, instructing_side: 9, joint: 2 }, months: 12 })));
  renderAt("/today");
  const footer = await screen.findByTestId("today-balance");
  expect(footer).toHaveTextContent(`This year: 14 for the ${word("other_side")}, 9 for the ${word("instructing_side")}, 2 for the ${word("joint")}.`);
});

test("no footer when there are no cases this year", async () => {
  signedIn();
  renderAt("/today");
  await screen.findByRole("heading", { level: 1 });
  expect(screen.queryByTestId("today-balance")).toBeNull();
});

test("Cases and Help are in the header, and Help opens a page with the support number", async () => {
  signedIn();
  server.use(http.get("/v1/cell", () => json({ storage_place: null, support_phone: "020 7946 0000", support_hours: "weekdays 8am to 6pm" })));
  const { user } = renderAt("/today");
  const header = await screen.findByRole("banner");
  expect(within(header).getByRole("link", { name: "Cases" })).toHaveAttribute("href", "/today#your-cases");
  await user.click(within(header).getByRole("link", { name: "Help" }));
  expect(await screen.findByRole("heading", { level: 1, name: "Help" })).toBeInTheDocument();
  expect(await screen.findByTestId("support-line")).toHaveTextContent("Call us on 020 7946 0000");
});
