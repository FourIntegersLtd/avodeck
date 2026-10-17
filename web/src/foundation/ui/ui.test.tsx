// Design-system components (web/AGENTS.md "Design system", docs/design/screens/Main.dc.html).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { renderUi } from "../../test/render";
import { Button, CaseHeader, StatusBadge, TextField } from ".";

const css = readFileSync(join(__dirname, "tokens.css"), "utf8");

test("body text is at least 17px and buttons at least 52px tall (U2)", () => {
  const body = css.match(/--text-body:\s*(\d+)px/);
  const button = css.match(/--button-min-height:\s*(\d+)px/);
  expect(Number(body?.[1])).toBeGreaterThanOrEqual(17);
  expect(Number(button?.[1])).toBeGreaterThanOrEqual(52);
  expect(css).toMatch(/--green:\s*#1E6B4E/i);
});

test("a button is type=button unless it submits", () => {
  renderUi(
    <>
      <Button>{"x"}</Button>
      <Button type="submit">{"y"}</Button>
    </>,
  );
  const [plain, submit] = screen.getAllByRole("button");
  expect(plain).toHaveAttribute("type", "button");
  expect(submit).toHaveAttribute("type", "submit");
});

test("a text field is labelled and shows its error to assistive technology", () => {
  renderUi(<TextField label="Email address" name="email" error="That didn't work." />);
  const input = screen.getByLabelText("Email address");
  expect(input).toHaveAttribute("aria-invalid", "true");
  expect(input).toHaveAccessibleDescription("That didn't work.");
});

test("the case header marks done steps with a tick and the current step for screen readers", () => {
  renderUi(<CaseHeader steps={[{ key: "prepare" }, { key: "read" }, { key: "write" }]} current="read" />);
  const [prepare, read, write] = screen.getAllByRole("listitem");
  expect(prepare).toHaveTextContent("✓Prepare");
  expect(read).toHaveAttribute("aria-current", "step");
  expect(read).toHaveTextContent("2Read");
  expect(write).not.toHaveAttribute("aria-current");
  expect(screen.getByRole("navigation")).toHaveAccessibleName("Steps for this case");
});

test("status badges use the glossary words, never colour alone", () => {
  renderUi(
    <>
      <StatusBadge status="checked" />
      <StatusBadge status="please_check" />
      <StatusBadge status="not_right" />
      <StatusBadge status="look" />
    </>,
  );
  for (const words of ["Checked by you", "Please check", "Not right", "Thing to look at"]) {
    expect(screen.getByText(words)).toBeInTheDocument();
  }
});
