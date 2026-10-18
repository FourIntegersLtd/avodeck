// The case frame: slices' case routes render inside it, get the case and its pack from it, and the
// pack step that holds the route's screen key is marked as the current step (rule 11: steps from packs).
import { act, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";
import { useCaseFrame } from "../foundation/lib/caseFrame";
import { http, json, server, signedIn } from "../test/api";
import { CASE_A, fixturePack, launchPack, PERSONAL_ORG, PERSON_ID, type Schemas } from "../test/fixtures";
import { AppProviders } from "./AppProviders";
import { CaseLayout } from "./CaseLayout";

const theCase: Schemas["Case"] = {
  id: CASE_A,
  organisation_id: PERSONAL_ORG,
  named_expert_id: PERSON_ID,
  jurisdiction: "gb-eaw",
  case_type: "clin-neg-breach",
  specialty: "general-practice",
  expert_role: "single",
  label: "Mrs A",
  party_role: "other_side",
  instructing_party_type: null,
  court_track: null,
  due_date: "2026-11-30",
  status: "open",
  on_hold: false,
  created_at: "2026-10-01T09:00:00Z",
  updated_at: "2026-10-01T09:00:00Z",
  allowed_actions: ["case.view_content"],
} as Schemas["Case"];

function Probe() {
  const { item, pack } = useCaseFrame();
  return <p data-testid="probe">{`${item.label}|${pack.locale}`}</p>;
}

function renderCase(path: string, screenKey: string) {
  const router = createMemoryRouter(
    [{ path: "/cases/:caseId", element: <CaseLayout />, children: [{ path: "probe", handle: { screen: screenKey }, element: <Probe /> }] }],
    { initialEntries: [path] },
  );
  return render(
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>,
  );
}

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("under the $name", ({ pack }) => {
  const steps = pack.workflow_steps as Array<{ key: string; screens?: string[] }>;
  const last = steps[steps.length - 1]!;

  test("a child route renders in the frame with the case and pack, and its step is current", async () => {
    signedIn({ pack });
    server.use(http.get(`/v1/cases/${CASE_A}`, () => json(theCase)));
    const screenKey = last.screens?.[0] ?? "probe";
    renderCase(`/cases/${CASE_A}/probe`, screenKey);
    expect(await screen.findByTestId("probe")).toHaveTextContent(`Mrs A|${pack.locale}`);
    expect(screen.queryByText("This part of the case is not ready yet.")).toBeNull();
    const current = screen.getAllByRole("listitem").find((li) => li.getAttribute("aria-current") === "step");
    expect(current).toBeDefined();
    expect(screen.getAllByRole("listitem").indexOf(current!)).toBe(steps.length - 1);
  });

  test("a screen key no step holds marks no step", async () => {
    signedIn({ pack });
    server.use(http.get(`/v1/cases/${CASE_A}`, () => json(theCase)));
    renderCase(`/cases/${CASE_A}/probe`, "no_such_screen");
    await screen.findByTestId("probe");
    expect(screen.getAllByRole("listitem").some((li) => li.hasAttribute("aria-current"))).toBe(false);
  });
});

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("time on case screens under the $name", ({ pack }) => {
  afterEach(() => vi.useRealTimers());
  const steps = pack.workflow_steps as Array<{ key: string; screens?: string[] }>;

  async function beatsFor(screenKey: string): Promise<unknown[]> {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const bodies: unknown[] = [];
    signedIn({ pack });
    server.use(
      http.get(`/v1/cases/${CASE_A}`, () => json(theCase)),
      http.post(`/v1/cases/${CASE_A}/time/heartbeat`, async ({ request }) => (bodies.push(await request.json()), json({ counted: true, stage: steps[0]!.key }))),
    );
    renderCase(`/cases/${CASE_A}/probe`, screenKey);
    await screen.findByTestId("probe");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    return bodies;
  }

  test("a screen a pack step holds sends its screen key every 30 seconds", async () => {
    const screenKey = steps[steps.length - 1]!.screens![0]!;
    const bodies = await beatsFor(screenKey);
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ screen: screenKey, visible: true });
  });

  test("a screen no step holds sends nothing", async () => {
    const bodies = await beatsFor("no_such_screen");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(bodies).toEqual([]);
  });
});
