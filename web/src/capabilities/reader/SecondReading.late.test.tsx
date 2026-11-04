// Screen 10, another reading: an answer for a page the expert has already left is never shown on the next page.
import { render, screen } from "@testing-library/react";
import { delay } from "msw";
import { expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { SecondReading } from "./SecondReading";

test("a late answer for page 5 is dropped once the expert has moved to page 6", async () => {
  server.use(
    http.get("/v1/cases/:caseId/pages/:n/second-reading", async ({ params }) => {
      if (params.n === "5") {
        await delay(150); // page 5's answer arrives after page 6's
        return json({ status: "done", reason: null, lines: [{ text: "Words from page five", confidence: 0.7 }] });
      }
      return json({ status: "done", reason: null, lines: [{ text: "Words from page six", confidence: 0.7 }] });
    }),
  );
  const { rerender } = render(<SecondReading caseId="c1" pageNo={5} />);
  rerender(<SecondReading caseId="c1" pageNo={6} />);
  expect(await screen.findByText("Words from page six")).toBeInTheDocument();
  await new Promise((resolve) => setTimeout(resolve, 250)); // let page 5's answer arrive
  expect(screen.queryByText("Words from page five")).toBeNull();
  expect(screen.getByText("Words from page six")).toBeInTheDocument();
});
