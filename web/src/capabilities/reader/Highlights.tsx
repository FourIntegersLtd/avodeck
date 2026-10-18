// Boxes over the page picture, one for each cited set of words. The server gives each box as fractions
// of the page, so they sit right at any zoom.
import type { Schemas } from "../../foundation/platform-client";
import { boxOf, pct } from "./data";

export function Highlights({ facts, focus }: { facts: Schemas["FactOut"][]; focus: string | null }) {
  return (
    <>
      {facts
        .filter((f) => f.status !== "rejected")
        .map((f) => {
          const b = boxOf(f);
          if (!b) return null;
          return (
            <span
              key={f.id}
              data-testid={`highlight-${f.id}`}
              className={focus === f.id ? "rd-mark rd-mark--focus" : "rd-mark"}
              style={{ left: pct(b.x0), top: pct(b.y0), width: pct(b.x1 - b.x0), height: pct(b.y1 - b.y0) }}
              aria-hidden="true"
            />
          );
        })}
    </>
  );
}
