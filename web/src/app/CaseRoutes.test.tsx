// Slice screens mount inside the case frame: CaseLayout renders the matched child route (an <Outlet/>)
// and shows the "coming" notice only when no child matches. Slice C's routes are found by convention
// (capabilityRoutes.ts): setupRoutes at the top level, setupCaseRoutes inside the case frame.
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { expect, test } from "vitest";
import { setupCaseRoutes, setupRoutes } from "../capabilities/setup";
import { registerScreen } from "../foundation/lib/screens";
import { http, json, server, signedIn } from "../test/api";
import { launchPack } from "../test/fixtures";
import { AppProviders } from "./AppProviders";
import { CaseLayout } from "./CaseLayout";
import { capabilityCaseRoutes, capabilityRoutes } from "./capabilityRoutes";

const CASE = "11111111-1111-4111-8111-111111111111";

function renderCase(path: string, stage: string | null = null) {
  signedIn();
  server.use(http.get("/v1/cases/:caseId", () => json({ id: CASE, label: "Case Zed", allowed_actions: [], stage })));
  const router = createMemoryRouter(
    [
      {
        path: "cases/:caseId",
        element: <CaseLayout />,
        children: [
          { path: "child", element: <p>Child screen</p> },
          { path: "conflicts", element: <p>Conflicts screen</p> },
        ],
      },
    ],
    { initialEntries: [path] },
  );
  render(
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>,
  );
}

test("a child route renders inside the case frame", async () => {
  renderCase(`/cases/${CASE}/child`);
  expect(await screen.findByText("Child screen")).toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 1, name: "Case Zed" })).toBeInTheDocument();
  expect(screen.queryByText("This part of the case is not ready yet.")).toBeNull();
});

test("the case itself opens the first registered screen of the step the case is at", async () => {
  registerScreen("new_case", () => "/cases/new");
  registerScreen("conflicts", ({ caseId }) => `/cases/${caseId}/conflicts`);
  renderCase(`/cases/${CASE}`, "prepare");
  expect(await screen.findByText("Conflicts screen")).toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 1, name: "Case Zed" })).toBeInTheDocument();
  expect(screen.queryByText("This part of the case is not ready yet.")).toBeNull();
});

test("the case itself shows the coming notice only when its step has no screen yet", async () => {
  renderCase(`/cases/${CASE}`, null);
  expect(await screen.findByRole("heading", { level: 1, name: "Case Zed" })).toBeInTheDocument();
  expect(screen.getByText("This part of the case is not ready yet.")).toBeInTheDocument();
  expect(screen.queryByText("Child screen")).toBeNull();
});

test("slice C's signed-in and case routes are mounted by convention", () => {
  expect(capabilityRoutes).toEqual(expect.arrayContaining(setupRoutes));
  expect(capabilityCaseRoutes).toEqual(expect.arrayContaining(setupCaseRoutes));
  for (const r of setupCaseRoutes) expect(capabilityRoutes).not.toContain(r);
});

test("every case route names a screen key the launch pack's steps hold, so the frame marks its step and keeps time", () => {
  const held = new Set((launchPack.workflow_steps as Array<{ screens?: string[] }>).flatMap((s) => s.screens ?? []));
  const named = capabilityCaseRoutes.map((r) => (r.handle as { screen?: string } | undefined)?.screen).filter((k): k is string => Boolean(k));
  expect(named.filter((k) => !held.has(k))).toEqual([]);
});
