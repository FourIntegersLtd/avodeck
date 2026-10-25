// ADR 0025: sign-in is password only, so a risky action (rule 10) is where the expert first meets the
// code. A session with no code yet answers 403 mfa_required: with mfa_enrolled false the prompt sets the
// authenticator up (picture, key, code, the backup codes once) and then carries on with the action; with
// mfa_enrolled true it asks for the code, as for step_up_required.
import { screen } from "@testing-library/react";
import { useState } from "react";
import { expect, test } from "vitest";
import { apiError, http, json, server } from "../../test/api";
import { axeViolations, renderUi } from "../../test/render";
import { t } from "../i18n";
import { api, unwrap } from "../platform-client";
import { useStepUp } from "./stepUp";

const revokeAll = () => unwrap(api.POST("/v1/me/sessions/revoke-all"));

function Probe({ wrapped }: { wrapped: boolean }) {
  const run = useStepUp();
  const [result, setResult] = useState("none");
  const go = () =>
    (wrapped ? run(revokeAll).then((r) => (r === undefined ? "cancelled" : "done")) : revokeAll().then(() => "done")).then(setResult, (e: { code?: string }) =>
      setResult(`error ${e.code}`),
    );
  return (
    <>
      <button onClick={() => void go()}>Do it</button>
      <p>result: {result}</p>
    </>
  );
}

function needsSetupOnce() {
  let calls = 0;
  server.use(
    http.post("/v1/me/sessions/revoke-all", () => (++calls === 1 ? apiError(403, "mfa_required", { mfa_enrolled: false }) : new Response(null, { status: 204 }))),
    http.post("/v1/auth/mfa/enroll", () => json({ qr_code: "data:image/svg+xml;base64,PHN2Zy8+", secret: "JBSWY3DPEHPK3PXP", uri: "otpauth://totp/x" })),
    http.post("/v1/auth/mfa/verify", () => json({ status: "signed_in", recovery_codes: ["aaaa-bbbb", "cccc-dddd"] })),
  );
  return () => calls;
}

for (const wrapped of [false, true]) {
  test(`with no authenticator yet, a risky action sets it up, shows the backup codes once, then carries on (${wrapped ? "useStepUp" : "any call"})`, async () => {
    const calls = needsSetupOnce();
    const { user, container } = renderUi(<Probe wrapped={wrapped} />);
    await user.click(screen.getByRole("button", { name: "Do it" }));
    expect(await screen.findByRole("heading", { name: t("step_up.setup.title") })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: t("sign_in.enrol.qr_alt") })).toBeInTheDocument();
    expect(screen.getByText("JBSWY3DPEHPK3PXP")).toBeInTheDocument();
    expect(await axeViolations(container)).toEqual([]);
    await user.type(screen.getByLabelText(t("ui.code_label")), "123456");
    await user.click(screen.getByRole("button", { name: t("step_up.submit") }));
    expect(await screen.findByRole("heading", { name: t("sign_in.codes.title") })).toBeInTheDocument();
    expect(screen.getByText("aaaa-bbbb")).toBeInTheDocument();
    expect(screen.getByText("cccc-dddd")).toBeInTheDocument();
    expect(screen.getByText("result: none")).toBeInTheDocument(); // the action waits until the codes are saved
    await user.click(screen.getByRole("button", { name: t("sign_in.codes.saved") }));
    expect(await screen.findByText("result: done")).toBeInTheDocument();
    expect(screen.queryByText("aaaa-bbbb")).not.toBeInTheDocument();
    expect(calls()).toBe(2);
  });
}

test("with an authenticator but no code yet this session, a risky action asks for the code and carries on", async () => {
  let calls = 0;
  server.use(
    http.post("/v1/me/sessions/revoke-all", () => (++calls === 1 ? apiError(403, "mfa_required", { mfa_enrolled: true }) : new Response(null, { status: 204 }))),
    http.post("/v1/auth/mfa/verify", () => json({ status: "signed_in", recovery_codes: null })),
  );
  const { user } = renderUi(<Probe wrapped={false} />);
  await user.click(screen.getByRole("button", { name: "Do it" }));
  expect(await screen.findByRole("heading", { name: t("step_up.title") })).toBeInTheDocument();
  await user.type(screen.getByLabelText(t("ui.code_label")), "123456");
  await user.click(screen.getByRole("button", { name: t("step_up.submit") }));
  expect(await screen.findByText("result: done")).toBeInTheDocument();
  expect(calls).toBe(2);
});

test("cancelling the set-up changes nothing and says so", async () => {
  needsSetupOnce();
  const { user } = renderUi(<Probe wrapped={true} />);
  await user.click(screen.getByRole("button", { name: "Do it" }));
  await screen.findByRole("heading", { name: t("step_up.setup.title") });
  await user.click(screen.getByRole("button", { name: t("step_up.cancel") }));
  expect(await screen.findByText(t("step_up.cancelled"))).toBeInTheDocument();
  expect(screen.getByText("result: cancelled")).toBeInTheDocument();
});

test("the set-up words are plain and say when the code is asked for", () => {
  expect(t("step_up.setup.title")).toBe("Set up the code on your phone");
  expect(t("step_up.setup.body")).toBe(
    "For your safety, we ask for a 6-digit code from your phone before important steps, such as signing or sending a report. You only set this up once.",
  );
});
