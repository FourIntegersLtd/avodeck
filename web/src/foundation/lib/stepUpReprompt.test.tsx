// Rule 10, plan phase 4: whenever any API call answers 403 step_up_required, the code prompt opens where
// the person is, and the same request is sent again once after the code is accepted. No screen needs to
// wrap its call; cancelling (or the code being asked for again) shows that nothing was changed.
import { screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { expect, test } from "vitest";
import { apiError, http, json, server } from "../../test/api";
import { renderUi } from "../../test/render";
import { t } from "../i18n";
import { api, unwrap } from "../platform-client";
import { useStepUp } from "./stepUp";

const CASE = "11111111-1111-4111-8111-111111111111";
const PATH = "/v1/cases/:caseId/support-access";

const grant = () =>
  unwrap(api.POST("/v1/cases/{case_id}/support-access", { params: { path: { case_id: CASE } }, body: { reason: "page_problem" } as never }));

function Plain() {
  const [result, setResult] = useState("none");
  return (
    <>
      <button onClick={() => void grant().then(() => setResult("done"), (e: { code?: string }) => setResult(`error ${e.code}`))}>Do it</button>
      <p>result: {result}</p>
    </>
  );
}

function Wrapped() {
  const run = useStepUp();
  const [result, setResult] = useState("none");
  return (
    <>
      <button onClick={() => void run(grant).then((r) => setResult(r ? "done" : "cancelled"))}>Do it</button>
      <p>result: {result}</p>
    </>
  );
}

async function enterCode(user: ReturnType<typeof renderUi>["user"]) {
  await user.type(await screen.findByLabelText(t("ui.code_label")), "123456");
  await user.click(screen.getByRole("button", { name: t("step_up.submit") }));
}

test("a plain call that needs the code opens the prompt, then sends the same request again once", async () => {
  const bodies: unknown[] = [];
  let verified = false;
  server.use(
    http.post(PATH, async ({ request }) => {
      bodies.push(await request.json());
      return verified ? json({ id: "g1" }, 201) : apiError(403, "step_up_required");
    }),
    http.post("/v1/auth/mfa/verify", () => {
      verified = true;
      return json({ status: "signed_in", recovery_codes: null });
    }),
  );
  const { user } = renderUi(<Plain />);
  await user.click(screen.getByRole("button", { name: "Do it" }));
  expect(await screen.findByRole("heading", { name: t("step_up.title") })).toBeInTheDocument();
  await enterCode(user);
  expect(await screen.findByText("result: done")).toBeInTheDocument();
  expect(bodies).toEqual([{ reason: "page_problem" }, { reason: "page_problem" }]);
  expect(screen.queryByText(t("step_up.cancelled"))).not.toBeInTheDocument();
});

test("cancelling sends nothing again and says nothing was changed", async () => {
  let calls = 0;
  server.use(http.post(PATH, () => (calls++, apiError(403, "step_up_required"))));
  const { user } = renderUi(<Plain />);
  await user.click(screen.getByRole("button", { name: "Do it" }));
  await user.click(await screen.findByRole("button", { name: t("step_up.cancel") }));
  expect(await screen.findByText(t("step_up.cancelled"))).toBeInTheDocument();
  expect(await screen.findByText("result: error step_up_required")).toBeInTheDocument();
  expect(calls).toBe(1);
});

test("the request is sent again only once, even if the code is still asked for", async () => {
  let calls = 0;
  server.use(
    http.post(PATH, () => (calls++, apiError(403, "step_up_required"))),
    http.post("/v1/auth/mfa/verify", () => json({ status: "signed_in", recovery_codes: null })),
  );
  const { user } = renderUi(<Plain />);
  await user.click(screen.getByRole("button", { name: "Do it" }));
  await enterCode(user);
  expect(await screen.findByText(t("step_up.cancelled"))).toBeInTheDocument();
  expect(await screen.findByText("result: error step_up_required")).toBeInTheDocument();
  expect(calls).toBe(2);
  expect(screen.queryByRole("heading", { name: t("step_up.title") })).not.toBeInTheDocument();
});

test("a screen that already wraps its call in useStepUp is asked for the code once, not twice", async () => {
  let calls = 0;
  server.use(http.post(PATH, () => (calls++, apiError(403, "step_up_required"))));
  const { user } = renderUi(<Wrapped />);
  await user.click(screen.getByRole("button", { name: "Do it" }));
  await user.click(await screen.findByRole("button", { name: t("step_up.cancel") }));
  expect(await screen.findByText("result: cancelled")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: t("step_up.title") })).not.toBeInTheDocument();
  expect(calls).toBe(1);
});

test("two calls that need the code at the same time share one prompt", async () => {
  let verified = false;
  let calls = 0;
  server.use(
    http.post(PATH, () => (calls++, verified ? json({ id: "g" }, 201) : apiError(403, "step_up_required"))),
    http.post("/v1/auth/mfa/verify", () => {
      verified = true;
      return json({ status: "signed_in", recovery_codes: null });
    }),
  );
  function Two() {
    const [n, setN] = useState(0);
    return (
      <>
        <button onClick={() => void Promise.all([grant(), grant()]).then((r) => setN(r.length))}>Do both</button>
        <p>done: {n}</p>
      </>
    );
  }
  const { user } = renderUi(<Two />);
  await user.click(screen.getByRole("button", { name: "Do both" }));
  await waitFor(() => expect(screen.getAllByRole("heading", { name: t("step_up.title") })).toHaveLength(1));
  await enterCode(user);
  expect(await screen.findByText("done: 2")).toBeInTheDocument();
  expect(calls).toBe(4);
});

test("sign-in and code calls are never re-prompted", async () => {
  let calls = 0;
  server.use(http.post("/v1/auth/mfa/verify", () => (calls++, apiError(403, "step_up_required"))));
  const err = await unwrap(api.POST("/v1/auth/mfa/verify", { body: { code: "123456" } })).catch((e: unknown) => e);
  expect(err).toMatchObject({ status: 403, code: "step_up_required" });
  expect(calls).toBe(1);
});
