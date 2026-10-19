// The colleague's spot-check page: no sign-in. The emailed code opens it; each cited value is agreed with or not.
import { screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { axeViolations, renderAt } from "../../test/render";
import { REPORT_MESSAGES as M } from "./words";

const FACT_A = "a1111111-1111-4111-8111-111111111111";
const FACT_B = "a2222222-2222-4222-8222-222222222222";
const url = "/spot-check/tok123";
const view = (over: Record<string, unknown> = {}) => ({
  status: "ok", expires_at: "2026-10-25T10:00:00Z", responded: false,
  items: [{ fact_id: FACT_A, value: "Ferritin 9", crop: "iVBORw0KGgo=" }, { fact_id: FACT_B, value: "review 3/52", crop: null }], ...over,
});

async function enterCode(user: ReturnType<typeof renderAt>["user"]) {
  await user.type(await screen.findByLabelText(M["report.colleague.code"]!), "123456");
  await user.click(screen.getByRole("button", { name: M["report.colleague.open"]! }));
}

test("the code opens the link, the values and pictures show, and the answers are sent", async () => {
  let header: string | null = null;
  let posted: unknown = null;
  server.use(
    http.get("/v1/spot-checks/:token", ({ request }) => { header = request.headers.get("x-spot-check-code"); return json(view() as never); }),
    http.post("/v1/spot-checks/:token/responses", async ({ request }) => { posted = await request.json(); return json({ status: "ok" }); }),
  );
  const { user, container } = renderAt(url);
  await enterCode(user);
  expect(await screen.findByText("Ferritin 9")).toBeInTheDocument();
  expect(header).toBe("123456");
  expect(screen.getByRole("img", { name: M["report.colleague.crop"]! })).toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
  const agree = screen.getAllByRole("radio", { name: M["report.colleague.agree"]! });
  const differ = screen.getAllByRole("radio", { name: M["report.colleague.differ"]! });
  await user.click(agree[0]!);
  await user.click(differ[1]!);
  await user.click(screen.getByRole("button", { name: M["report.colleague.send"]! }));
  expect(await screen.findByText(M["report.colleague.thanks"]!)).toBeInTheDocument();
  expect(posted).toEqual({ code: "123456", answers: [{ fact_id: FACT_A, agrees: true }, { fact_id: FACT_B, agrees: false, reason: "does_not_match" }] });
});

test("send waits until every item is answered", async () => {
  server.use(http.get("/v1/spot-checks/:token", () => json(view() as never)));
  const { user } = renderAt(url);
  await enterCode(user);
  await user.click((await screen.findAllByRole("radio", { name: M["report.colleague.agree"]! }))[0]!);
  expect(screen.getByRole("button", { name: M["report.colleague.send"]! })).toBeDisabled();
});

test.each([["expired", "report.colleague.expired"], ["unknown", "report.colleague.unknown"], ["locked", "report.colleague.locked"]])(
  "a %s link says so", async (status, key) => {
    server.use(http.get("/v1/spot-checks/:token", () => json({ status, items: [], responded: false } as never)));
    const { user } = renderAt(url);
    await enterCode(user);
    expect(await screen.findByText(M[key]!)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: M["report.colleague.send"]! })).toBeNull();
  },
);

test("a wrong code asks again and a link already answered says thanks", async () => {
  let calls = 0;
  server.use(http.get("/v1/spot-checks/:token", () => json((++calls === 1 ? { status: "bad_code", items: [], responded: false } : view({ responded: true })) as never)));
  const { user } = renderAt(url);
  await enterCode(user);
  expect(await screen.findByText(M["report.colleague.bad_code"]!)).toBeInTheDocument();
  await user.clear(screen.getByLabelText(M["report.colleague.code"]!));
  await enterCode(user);
  await waitFor(() => expect(screen.getByText(M["report.colleague.thanks"]!)).toBeInTheDocument());
});
