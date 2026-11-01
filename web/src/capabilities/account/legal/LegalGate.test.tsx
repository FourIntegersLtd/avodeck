// "Before you start" (S28 gate): agreeing goes through the generated client (CSRF/Origin handled
// for free), and a failed accept shows a message instead of leaving Continue looking like it did
// nothing (it used to hand-roll fetch and swallow the error).
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { expect, test } from "vitest";
import { apiError, http, json, server } from "../../../test/api";
import { axeViolations } from "../../../test/render";
import { LegalGate } from "./LegalGate";

function renderGate(onDone?: () => void) {
  const user = userEvent.setup();
  const view = render(
    <MemoryRouter initialEntries={["/agree"]}>
      <LegalGate onDone={onDone} />
    </MemoryRouter>,
  );
  return { ...view, user };
}

async function agree(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByLabelText(/I have read and agree/));
  await user.click(screen.getByRole("button", { name: "Continue" }));
}

test("shows the Avodeck mark but no navigation", async () => {
  const { container } = renderGate();
  expect(await screen.findByRole("link", { name: "Avodeck home page" })).toBeInTheDocument();
  expect(screen.queryByRole("navigation")).toBeNull();
  expect(await axeViolations(container)).toEqual([]);
});

test("agreeing calls the generated client and tells the caller it's done", async () => {
  let called = false;
  server.use(
    http.post("/v1/legal-agreements/accept", () => {
      called = true;
      return json({ accepted: true });
    }),
  );
  let done = false;
  const { user } = renderGate(() => {
    done = true;
  });
  await agree(user);
  await waitFor(() => expect(done).toBe(true));
  expect(called).toBe(true);
});

test("a failed accept shows an error and lets the person try again, instead of going quiet", async () => {
  server.use(http.post("/v1/legal-agreements/accept", () => apiError(500, "server_error")));
  let done = false;
  const { user } = renderGate(() => {
    done = true;
  });
  await agree(user);
  expect(await screen.findByText("Something went wrong. Please try again.")).toBeInTheDocument();
  expect(done).toBe(false);

  // The button isn't stuck busy: a retry with the server now working gets the person through.
  const button = screen.getByRole("button", { name: "Continue" });
  expect(button).not.toBeDisabled();
  server.use(http.post("/v1/legal-agreements/accept", () => json({ accepted: true })));
  await user.click(button);
  await waitFor(() => expect(done).toBe(true));
});
