// Rule 10's code prompt never leaves the person guessing: cancelling (or the code being asked for again
// after it was given) shows that nothing was changed, without the screen that asked doing anything.
import { screen } from "@testing-library/react";
import { useState } from "react";
import { expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { renderUi } from "../../test/render";
import { t } from "../i18n";
import { ApiError } from "../platform-client";
import { useStepUp } from "./stepUp";

function Probe({ action }: { action: () => Promise<string> }) {
  const run = useStepUp();
  const [result, setResult] = useState("none");
  return (
    <>
      <button onClick={() => void run(action).then((r) => setResult(r ?? "cancelled"))}>Do it</button>
      <p>result: {result}</p>
    </>
  );
}

const needsCode = () => Promise.reject(new ApiError(403, "step_up_required"));

test("cancelling the code shows that nothing was changed", async () => {
  const { user } = renderUi(<Probe action={needsCode} />);
  await user.click(screen.getByRole("button", { name: "Do it" }));
  await user.click(await screen.findByRole("button", { name: t("step_up.cancel") }));
  expect(await screen.findByText(t("step_up.cancelled"))).toBeInTheDocument();
  expect(screen.getByText("result: cancelled")).toBeInTheDocument();
  expect(t("step_up.cancelled")).toBe("Nothing was changed. You need your security code to do this.");
});

test("the notice goes once the person tries again", async () => {
  let calls = 0;
  const action = () => (++calls === 1 ? needsCode() : Promise.resolve("done"));
  const { user } = renderUi(<Probe action={action} />);
  await user.click(screen.getByRole("button", { name: "Do it" }));
  await user.click(await screen.findByRole("button", { name: t("step_up.cancel") }));
  await screen.findByText(t("step_up.cancelled"));
  await user.click(screen.getByRole("button", { name: "Do it" }));
  expect(await screen.findByText("result: done")).toBeInTheDocument();
  expect(screen.queryByText(t("step_up.cancelled"))).not.toBeInTheDocument();
});

test("when the code is asked for again after it was given, the same notice shows", async () => {
  server.use(http.post("/v1/auth/mfa/verify", () => json({ aal: "aal2" })));
  const { user } = renderUi(<Probe action={needsCode} />);
  await user.click(screen.getByRole("button", { name: "Do it" }));
  await user.type(await screen.findByLabelText(t("ui.code_label")), "123456");
  await user.click(screen.getByRole("button", { name: t("step_up.submit") }));
  expect(await screen.findByText(t("step_up.cancelled"))).toBeInTheDocument();
  expect(screen.getByText("result: cancelled")).toBeInTheDocument();
});
