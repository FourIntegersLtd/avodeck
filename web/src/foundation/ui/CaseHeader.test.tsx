import { screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test } from "vitest";
import { registerScreen, resetScreens } from "../lib/screens";
import { renderUi } from "../../test/render";
import { CaseHeader } from ".";

afterEach(() => resetScreens());

test("a step pill opens the first screen inside this case, never a case-less form", () => {
  registerScreen("new_case", () => "/cases/new");
  registerScreen("conflicts", ({ caseId }) => `/cases/${caseId}/conflicts`);
  renderUi(
    <MemoryRouter>
      <CaseHeader bar caseId="c1" steps={[{ key: "prepare", screens: ["new_case", "conflicts"] }]} current="prepare" />
    </MemoryRouter>,
  );
  expect(screen.getByRole("link", { name: /Prepare/ })).toHaveAttribute("href", "/cases/c1/conflicts");
});
