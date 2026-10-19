// The balance shows every party role the same way, with the pack's own word: no role is named in code (rule 11).
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { renderUi } from "../../test/render";
import { Balance } from "./shared";

test("every role is shown with the pack's word, including one the code has never heard of", async () => {
  server.use(http.get("/v1/me/stats/balance", () => json({ counts: { zz_joint_word: 2, zz_side: 5 }, months: 12 })));
  renderUi(<Balance words={{ zz_joint_word: "zz neutral party", zz_side: "zz other side" }} />);
  const region = await screen.findByRole("region", { name: "Your balance over the last 12 months" });
  expect(await within(region).findByText("for the zz neutral party")).toBeInTheDocument();
  expect(within(region).getByText("for the zz other side")).toBeInTheDocument();
});

test("a role called joint is not treated differently from any other", async () => {
  server.use(http.get("/v1/me/stats/balance", () => json({ counts: { joint: 3 }, months: 12 })));
  renderUi(<Balance words={{ joint: "zz shared word" }} />);
  const region = await screen.findByRole("region", { name: "Your balance over the last 12 months" });
  expect(await within(region).findByText("for the zz shared word")).toBeInTheDocument();
});
