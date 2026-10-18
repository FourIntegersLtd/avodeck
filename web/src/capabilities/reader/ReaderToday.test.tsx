// Today (S03) shows slice B's items in words and opens slice B's screens.
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { signedIn } from "../../test/api";
import { CASE_A, caseCard, today } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import "./index";

test("pages left to read and pages to type out open S09 and S10", async () => {
  signedIn({
    today: today({
      cases: [caseCard()],
      needs_you: [
        { kind: "pages_to_read", case_id: CASE_A, rank: 20, label_key: "reader.today.pages_to_read", action_key: "reader.today.pages_to_read_action", screen: "S09", count: 127, minutes: 40, due_at: null, params: {} },
        { kind: "pages_to_type", case_id: CASE_A, rank: 30, label_key: "reader.today.pages_to_type", action_key: "reader.today.pages_to_type_action", screen: "S10", count: 1, minutes: null, due_at: null, params: {} },
      ],
    }),
  });
  renderAt("/today");
  expect(await screen.findByText("127 pages left to read")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Carry on reading" })).toHaveAttribute("href", `/cases/${CASE_A}/read`);
  expect(screen.getByText("1 page to type out")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Type them out" })).toHaveAttribute("href", `/cases/${CASE_A}/read/couldnt-read`);
});
