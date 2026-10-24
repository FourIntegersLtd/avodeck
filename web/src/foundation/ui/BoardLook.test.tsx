// The shell and design-system parts against the canvas boards (docs/design/screens/*.dc.html):
// one highlighted place in the header, the case bar's step pills, the focused header of S04,
// amber callouts outlined on white, the grey segmented switch, the choice pills and the 34px titles.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test } from "vitest";
import { registerScreen, resetScreens } from "../lib/screens";
import { signedIn } from "../../test/api";
import { renderAt, renderUi } from "../../test/render";
import { Callout, CaseHeader, FocusedHeader } from ".";

const styles = readFileSync(join(__dirname, "styles.css"), "utf8");
const tokens = readFileSync(join(__dirname, "tokens.css"), "utf8");
/** The declarations of the last rule whose selector list is exactly `selector`. */
const rule = (selector: string) => {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const found = [...styles.matchAll(new RegExp(`(?:^|\\n|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`, "g"))];
  return found.length ? found[found.length - 1]![1]! : "";
};

afterEach(() => resetScreens());

test("only the place you are in is highlighted in the header (S03 shows Today alone)", async () => {
  registerScreen("cases", () => "/today#your-cases");
  signedIn();
  renderAt("/today");
  const nav = await screen.findByRole("navigation", { name: "Main" });
  await within(nav).findByRole("link", { name: "Cases" });
  expect(within(nav).getByRole("link", { name: "Today" })).toHaveAttribute("aria-current", "page");
  expect(within(nav).getByRole("link", { name: "Cases" })).not.toHaveAttribute("aria-current");
});

test("following the Cases link highlights Cases, not Today", async () => {
  registerScreen("cases", () => "/today#your-cases");
  signedIn();
  renderAt("/today#your-cases");
  const nav = await screen.findByRole("navigation", { name: "Main" });
  expect(await within(nav).findByRole("link", { name: "Cases" })).toHaveAttribute("aria-current", "page");
  expect(within(nav).getByRole("link", { name: "Today" })).not.toHaveAttribute("aria-current");
});

test("a finished step shows a green tick and no number; the current step keeps its number", () => {
  const { container } = renderUi(
    <MemoryRouter>
      <CaseHeader bar caseId="c1" steps={[{ key: "prepare" }, { key: "read" }, { key: "write" }]} current="read" title={<span>{"Mrs A"}</span>} />
    </MemoryRouter>,
  );
  const done = container.querySelector(".av-step--done")!;
  expect(done.querySelector("svg")).not.toBeNull();
  expect(done.querySelector(".av-step__marker")).toBeNull();
  expect(done).not.toHaveTextContent("1");
  expect(container.querySelector(".av-step--current .av-step__marker")).toHaveTextContent("2");
  expect(rule(".av-steps")).toMatch(/background:\s*var\(--well\)/); // calm design: steps sit in a grey track
});

test("the focused header (S04) has the logo, the task's name and Cancel, and no menu", () => {
  renderUi(
    <MemoryRouter>
      <FocusedHeader title="New case" cancelTo="/today" />
    </MemoryRouter>,
  );
  const header = screen.getByRole("banner");
  expect(within(header).getByRole("link", { name: "Avodeck home page" })).toBeInTheDocument();
  expect(within(header).getByText("New case")).toBeInTheDocument();
  expect(within(header).getByRole("link", { name: "Cancel" })).toHaveAttribute("href", "/today");
  expect(within(header).queryByRole("navigation")).toBeNull();
});

test("a callout is outlined in amber on white, with an amber icon tile", () => {
  const { container } = renderUi(<Callout title="4 pages may belong to someone else.">{"Nothing is hidden until you check."}</Callout>);
  expect(container.querySelector(".av-callout .av-icon-tile--amber svg")).not.toBeNull();
  const look = rule(".av-callout");
  expect(look).toMatch(/background:\s*var\(--surface\)/);
  expect(look).toMatch(/border:\s*2px solid var\(--amber-border\)/);
});

test("a segmented switch is a grey tray with the chosen option raised in white", () => {
  expect(rule(".av-segmented")).toMatch(/background:\s*var\(--surface-soft\)/);
  expect(rule(".av-segmented__option--selected")).toMatch(/background:\s*var\(--surface\)/);
  expect(rule(".av-segmented__option--selected")).toMatch(/box-shadow:/);
});

test("choice pills are rounded cards with a hairline border, mint when chosen", () => {
  expect(rule(".av-pill")).toMatch(/border-radius:\s*14px/);
  expect(rule(".av-pill")).toMatch(/border:\s*2px solid var\(--line\)/);
  expect(rule(".av-pill--selected")).toMatch(/background:\s*var\(--green-tint\)/);
});

test("a progress ring has a grey track and a leaf arc", () => {
  expect(rule(".av-ring__track")).toMatch(/stroke:\s*var\(--track\)/);
  expect(rule(".av-ring__arc")).toMatch(/stroke:\s*var\(--green-bright\)/);
  expect(tokens).toMatch(/--track:\s*#E9ECEA/i);
});

test("page titles are 34px, and a case screen's first heading under the case bar is drawn as its title", () => {
  expect(tokens).toMatch(/--text-h1:\s*34px/);
  expect(rule(".av-case-bar ~ .av-page > h2:first-of-type, .av-case-bar ~ .av-page > :first-child > h2:first-of-type, .av-case-bar ~ .av-page > :first-child > :first-child > h2:first-of-type")).toMatch(
    /font-size:\s*var\(--text-h1\)/,
  );
});

test("the sign-in form is spaced like the board: 28px between its parts", () => {
  expect(rule(".av-sign-in__form.av-stack > * + *")).toMatch(/margin-top:\s*28px/);
});
