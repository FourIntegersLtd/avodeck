// The case bar names the step the setup screens belong to, and S04 draws the focused header of its board.
import { screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { http, json, server, signedIn } from "../../test/api";
import { CASE_B, me } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { caseItem, casePack, combination, jurisdiction } from "./testing";

describe.each(["conflicts", "questions"])("the %s screen", (path) => {
  test("shows its step as the current one in the case bar", async () => {
    signedIn({ me: me(), pack: casePack("launch") });
    server.use(http.get("/v1/cases/:caseId", () => json(caseItem("launch", { id: CASE_B }))));
    renderAt(`/cases/${CASE_B}/${path}`);
    const steps = await screen.findByRole("navigation", { name: "Steps for this case" });
    expect(within(steps).getByRole("listitem", { current: "step" })).toHaveClass("av-step--current");
  });
});

test("the new case screen has the focused header: logo, task name and Cancel", async () => {
  signedIn({ me: me(), pack: casePack("launch") });
  server.use(
    http.get("/v1/packs", () => json([combination("launch")])),
    http.get("/v1/jurisdictions/:code", () => json(jurisdiction("launch"))),
  );
  const { container } = renderAt("/cases/new");
  await screen.findByRole("heading", { level: 1, name: "Start a new case" });
  const bar = container.querySelector(".av-focused-bar");
  expect(bar).not.toBeNull();
  expect(within(bar as HTMLElement).getByText("New case")).toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: "Cancel" })).toHaveLength(1);
});
