// The case frame: slices' case routes render inside it, get the case and its pack from it, and the
// pack step that holds the route's screen key is marked as the current step (rule 11: steps from packs).
import { act, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";
import { useCaseFrame } from "../foundation/lib/caseFrame";
import { registerScreen, resetScreens } from "../foundation/lib/screens";
import { http, json, server, signedIn } from "../test/api";
import { renderAt } from "../test/render";
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

describe("the case bar is one bar", () => {
  afterEach(() => resetScreens());

  test("logo, divider, case name, case type and due date, step links and the avatar are in one banner", async () => {
    const steps = launchPack.workflow_steps as Array<{ key: string; screens?: string[] }>;
    registerScreen(steps[1]!.screens![0]!, ({ caseId }) => `/cases/${caseId}/read-here`);
    signedIn();
    server.use(http.get(`/v1/cases/${CASE_A}`, () => json(theCase)));
    renderAt(`/cases/${CASE_A}`);
    const bar = await screen.findByRole("banner");
    expect(await within(bar).findByRole("heading", { level: 1, name: "Mrs A" })).toBeInTheDocument();
    expect(screen.getAllByRole("banner")).toHaveLength(1);
    expect(within(bar).getByRole("link", { name: "Avodeck home page" })).toBeInTheDocument();
    expect(bar.querySelector(".av-case-bar__divider")).not.toBeNull();
    expect(bar).toHaveTextContent(/due 30 Nov/);
    const nav = within(bar).getByRole("navigation", { name: "Steps for this case" });
    expect(within(nav).getAllByRole("listitem")).toHaveLength(steps.length);
    expect(within(bar).queryByRole("navigation", { name: "Main" })).toBeNull();
    expect(within(bar).getByRole("link", { name: "Account" })).toHaveAttribute("href", "/account");
  });

  test("each step is a link to the first registered screen of that step, and a plain pill when none is", async () => {
    const steps = launchPack.workflow_steps as Array<{ key: string; screens?: string[] }>;
    registerScreen(steps[1]!.screens![1]!, ({ caseId }) => `/cases/${caseId}/second`);
    signedIn();
    server.use(http.get(`/v1/cases/${CASE_A}`, () => json(theCase)));
    renderAt(`/cases/${CASE_A}`);
    const nav = await screen.findByRole("navigation", { name: "Steps for this case" });
    expect(within(nav).getByRole("link", { name: /Read/ })).toHaveAttribute("href", `/cases/${CASE_A}/second`);
    expect(within(nav).getAllByRole("link")).toHaveLength(1);
  });
});

describe("the case home", () => {
  afterEach(() => resetScreens());

  function homeRouter() {
    return createMemoryRouter(
      [{ path: "/cases/:caseId", element: <CaseLayout />, children: [{ path: "home", handle: { screen: "case_home" }, element: <p>Home screen</p> }] }],
      { initialEntries: [`/cases/${CASE_A}`] },
    );
  }

  test("opening a case lands on its home when a slice registers one", async () => {
    registerScreen("case_home", ({ caseId }) => `/cases/${caseId}/home`);
    signedIn();
    server.use(http.get(`/v1/cases/${CASE_A}`, () => json({ ...theCase, stage: "read" })));
    const router = homeRouter();
    render(
      <AppProviders>
        <RouterProvider router={router} />
      </AppProviders>,
    );
    expect(await screen.findByText("Home screen")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe(`/cases/${CASE_A}/home`);
  });

  test("the case name in the bar links to the home", async () => {
    registerScreen("case_home", ({ caseId }) => `/cases/${caseId}/home`);
    signedIn();
    server.use(http.get(`/v1/cases/${CASE_A}`, () => json(theCase)));
    const router = homeRouter();
    render(
      <AppProviders>
        <RouterProvider router={router} />
      </AppProviders>,
    );
    await screen.findByText("Home screen");
    const bar = await screen.findByRole("banner");
    expect(await within(bar).findByRole("link", { name: "Mrs A" })).toHaveAttribute("href", `/cases/${CASE_A}/home`);
  });

  test("the case name is plain text when no slice registers a home", async () => {
    signedIn();
    server.use(http.get(`/v1/cases/${CASE_A}`, () => json(theCase)));
    renderAt(`/cases/${CASE_A}`);
    const bar = await screen.findByRole("banner");
    expect(await within(bar).findByRole("heading", { level: 1, name: "Mrs A" })).toBeInTheDocument();
    expect(within(bar).queryByRole("link", { name: "Mrs A" })).toBeNull();
  });
});

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("the step's screens row in the frame under the $name", ({ pack }) => {
  afterEach(() => resetScreens());
  const steps = pack.workflow_steps as Array<{ key: string; screens?: string[] }>;
  const step = steps.find((s) => (s.screens ?? []).length >= 3)!;
  const screens = step.screens!;

  test("shows the current route's step screens, current one marked, others linked", async () => {
    for (const key of screens) registerScreen(key, ({ caseId }) => `/cases/${caseId}/s-${key}`);
    signedIn({ pack });
    server.use(http.get(`/v1/cases/${CASE_A}`, () => json(theCase)));
    const router = createMemoryRouter(
      [{ path: "/cases/:caseId", element: <CaseLayout />, children: screens.map((k) => ({ path: `s-${k}`, handle: { screen: k }, element: <p>{`Screen ${k}`}</p> })) }],
      { initialEntries: [`/cases/${CASE_A}/s-${screens[1]}`] },
    );
    render(
      <AppProviders>
        <RouterProvider router={router} />
      </AppProviders>,
    );
    await screen.findByText(`Screen ${screens[1]}`);
    const row = await screen.findByRole("navigation", { name: "Screens in this step" });
    const links = within(row).getAllByRole("link");
    expect(links).toHaveLength(screens.length);
    expect(links[1]).toHaveAttribute("aria-current", "page");
    expect(links[0]).toHaveAttribute("href", `/cases/${CASE_A}/s-${screens[0]}`);
  });
});
