// Messages: what the bell opens. Each message says what happened in words from the kind; the
// server never sends any case text beyond the case name the person can already see.
import { screen, waitFor, within } from "@testing-library/react";
import { beforeAll, expect, test } from "vitest";
import { registerMessages } from "../../foundation/i18n";
import { http, json, server, signedIn } from "../../test/api";
import { CASE_A, message, messages } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";

const FIRST = "88888888-8888-4888-8888-888888888888";
const SECOND = "99999999-9999-4999-8999-999999999999";

beforeAll(() => {
  // A slice names its message kinds with i18n keys.
  registerMessages({ "notifications.kind.zz.ready": "{{case}}: the pages are ready to read" });
});

function inbox() {
  signedIn({
    messages: messages({
      items: [
        message({ id: FIRST, kind: "zz.ready" }),
        message({ id: SECOND, kind: "something_new", read_at: "2026-10-16T09:00:00Z" }),
      ],
      unread_count: 1,
    }),
  });
}

test("lists messages in the kind's own words, with a plain fallback, and passes axe", async () => {
  inbox();
  const { container } = renderAt("/messages");
  expect(await screen.findByRole("heading", { level: 1, name: "Messages" })).toBeInTheDocument();
  const list = await screen.findByRole("list", { name: "Messages" });
  const items = within(list).getAllByRole("listitem");
  const [first, second] = items as [HTMLElement, HTMLElement];
  expect(first).toHaveTextContent("Mrs A: the pages are ready to read");
  expect(first).toHaveTextContent("New");
  expect(second).toHaveTextContent("Something changed in Mrs A");
  expect(within(first).getByRole("link", { name: "Open Mrs A" })).toHaveAttribute("href", `/cases/${CASE_A}`);
  expect(await axeViolations(container)).toEqual([]);
});

test("marking a message read tells the server", async () => {
  inbox();
  let marked = "";
  server.use(
    http.post("/v1/notifications/:id/read", ({ params }) => {
      marked = String(params.id);
      return json(message({ id: FIRST, kind: "zz.ready", read_at: new Date().toISOString() }));
    }),
  );
  const { user } = renderAt("/messages");
  await user.click(await screen.findByRole("button", { name: "Mark as read" }));
  await waitFor(() => expect(marked).toBe(FIRST));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Mark as read" })).not.toBeInTheDocument());
});

test("no messages says so", async () => {
  signedIn();
  renderAt("/messages");
  expect(await screen.findByText("You have no messages.")).toBeInTheDocument();
});
