// Today (S03) shows slice C's items in words and opens slice C's screens: the questions to check (S06),
// an incomplete profile (S02) and a new case.
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { signedIn } from "../../test/api";
import { CASE_A, caseCard, today } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import "./index";

test("questions waiting to be checked open What you've been asked", async () => {
  signedIn({
    today: today({
      cases: [caseCard()],
      needs_you: [
        { kind: "setup_questions_to_confirm", case_id: CASE_A, rank: 20, label_key: "setup.today.questions_to_confirm", action_key: "setup.today.questions_to_confirm_action", screen: "S06", count: 3 },
        { kind: "setup_profile_incomplete", case_id: CASE_A, rank: 30, label_key: "setup.today.profile_incomplete", action_key: "setup.today.profile_incomplete_action", screen: "S02", count: 2 },
      ],
    }),
  });
  renderAt("/today");
  expect(await screen.findByText("Mrs A: 3 questions to check")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Check the questions" })).toHaveAttribute("href", `/cases/${CASE_A}/questions`);
  expect(screen.getByText("Your profile has 2 things missing")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Finish your profile" })).toHaveAttribute("href", "/profile");
  expect(screen.getByRole("link", { name: "Start a new case" })).toHaveAttribute("href", "/cases/new");
});
