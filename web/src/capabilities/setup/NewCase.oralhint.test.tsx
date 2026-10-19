// S04 New case: the oral instructions box has no Dictate control, so its hint must not promise dictation.
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, json, server, signedIn } from "../../test/api";
import { me } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { casePack, combination, jurisdiction } from "./testing";

test("the oral instructions hint on the New case screen says to write them, not to dictate", async () => {
  signedIn({ me: me(), pack: casePack("launch") });
  server.use(
    http.get("/v1/packs", () => json([combination("launch")])),
    http.get("/v1/jurisdictions/:code", () => json(jurisdiction("launch"))),
  );
  renderAt("/cases/new");
  const box = await screen.findByLabelText("Were you told anything by phone that isn't in the letter?");
  expect(box).toHaveAccessibleDescription("You need to record oral instructions too. Write them in your own words.");
  expect(box.getAttribute("aria-describedby")).toBeTruthy();
  expect(document.body.textContent ?? "").not.toMatch(/dictate/i);
});
