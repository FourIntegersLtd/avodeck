import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { AuthLayout as OldPlace } from "../../app/auth/AuthLayout";
import { renderUi } from "../../test/render";
import { AuthLayout } from "./index";

test("AuthLayout is part of the design system, and the old place re-exports it", () => {
  expect(OldPlace).toBe(AuthLayout);
});

test("it frames one task with the promise beside it", () => {
  renderUi(
    <AuthLayout>
      <h1>Join</h1>
    </AuthLayout>,
  );
  expect(screen.getByRole("heading", { level: 1, name: "Join" })).toBeInTheDocument();
  expect(screen.getAllByRole("main")).toHaveLength(1);
});
