// S02 first visit: an empty profile still reads as five clear steps. A mint note says everything saves,
// each card says why we need it, the next step is outlined, empty cards show a dashed box with an Add button.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, HttpResponse, json, server, signedIn } from "../../test/api";
import { me } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { casePack, combination, jurisdiction } from "./testing";

const missing = () => HttpResponse.json({ error: { code: "not_found" } }, { status: 404 });

function openEmpty() {
  const pack = casePack("launch");
  signedIn({ me: me(), pack });
  server.use(
    http.get("/v1/me/profile", () =>
      HttpResponse.json(
        { full_name: "", profession: pack.profession, registration_body: pack.registration_bodies[0], registration_number: "", specialties: [], default_jurisdiction: jurisdiction("launch").code, default_retention: null, hourly_rate: null, currency: null },
        { headers: { etag: '"1"' } },
      ),
    ),
    http.get("/v1/packs", () => json([combination("launch")])),
    http.get("/v1/jurisdictions/:code", () => json(jurisdiction("launch"))),
    http.get("/v1/me/roles", () => json([])),
    http.get("/v1/me/training", () => json([])),
    http.get("/v1/me/template", missing),
    http.get("/v1/me/declaration", () => HttpResponse.json({ jurisdiction: "x", wording: pack.declarations, additions: [], locked: false }, { headers: { etag: '"1"' } })),
    http.get("/v1/me/signature/image", missing),
    http.get("/v1/me/stats/balance", () => json({ counts: {}, months: 12 })),
  );
}

test("an empty profile shows 0 of 5, the saves note, a why line per card and Done for now", async () => {
  openEmpty();
  const { container } = renderAt("/profile");
  expect(await screen.findByRole("img", { name: "0 of 5 parts done" })).toBeInTheDocument();
  expect(screen.getByText("Everything saves as you type. You can stop and come back at any time.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Done for now" })).toHaveAttribute("href", "/today");
  for (const name of ["About you", "Where you've practised", "Expert training", "Your report template", "Declaration and signature"]) {
    expect(screen.getByRole("region", { name })).toBeInTheDocument();
  }
  expect(within(screen.getByRole("region", { name: "Expert training" })).getByText("Courses and certificates. These are listed in your reports.")).toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
});

test("the first unfinished card is outlined and the others are not", async () => {
  openEmpty();
  renderAt("/profile");
  const about = await screen.findByRole("region", { name: "About you" });
  expect(about).toHaveAttribute("aria-current", "step");
  expect(screen.getByRole("region", { name: "Expert training" })).not.toHaveAttribute("aria-current");
});

test("empty cards show a dashed box with an Add button that opens the form", async () => {
  openEmpty();
  const { user } = renderAt("/profile");
  const roles = await screen.findByRole("region", { name: "Where you've practised" });
  expect(within(roles).getByText("No roles added yet")).toBeInTheDocument();
  expect(within(roles).queryByLabelText("Your role")).toBeNull();
  await user.click(within(roles).getByRole("button", { name: "Add a role" }));
  expect(within(roles).getByLabelText("Your role")).toBeInTheDocument();

  const training = screen.getByRole("region", { name: "Expert training" });
  expect(within(training).getByText("No training added yet")).toBeInTheDocument();
  expect(within(training).queryByLabelText("Course name")).toBeNull();
  await user.click(within(training).getByRole("button", { name: "Add training" }));
  expect(within(training).getByLabelText("Course name")).toBeInTheDocument();

  const template = screen.getByRole("region", { name: "Your report template" });
  expect(within(template).getByText("Using the standard layout")).toBeInTheDocument();
  expect(within(template).getByText("Choose your Word template")).toBeInTheDocument();
});
