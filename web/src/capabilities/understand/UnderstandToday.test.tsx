// Today (S03) shows slice D's item in words and opens Things to look at (S13).
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { signedIn } from "../../test/api";
import { CASE_A, caseCard, today } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import "./index";

test("things to look at open S13", async () => {
  signedIn({
    today: today({
      cases: [caseCard()],
      needs_you: [{ kind: "understand_things_to_look_at", case_id: CASE_A, rank: 30, label_key: "understand.today.things_to_look_at", action_key: "understand.today.things_to_look_at_action", screen: "S13", count: 2 }],
    }),
  });
  renderAt("/today");
  expect(await screen.findByText("Mrs A: 2 things to look at")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Look at them" })).toHaveAttribute("href", `/cases/${CASE_A}/things-to-look-at`);
});
