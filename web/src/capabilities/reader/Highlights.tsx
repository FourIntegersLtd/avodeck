// Boxes over the page picture, one for each cited set of words. The server gives each box as fractions
// of the page, so they sit right at any zoom.
import type { Schemas } from "../../foundation/platform-client";
import { pct, type Box } from "./data";

export function Highlights({ highlights, focus }: { highlights: Schemas["Highlight"][]; focus: string | null }) {
  return (
    <>
      {highlights
        .map((f) => {
          const b = f.bbox as Partial<Box> | null;
          if (!b || [b.x0, b.y0, b.x1, b.y1].some((v) => typeof v !== "number")) return null;
          const { x0, y0, x1, y1 } = b as Box;
          return (
            <span
              key={f.id}
              data-testid={`highlight-${f.id}`}
              className={focus === f.id ? "rd-mark rd-mark--focus" : "rd-mark"}
              style={{ left: pct(x0), top: pct(y0), width: pct(x1 - x0), height: pct(y1 - y0) }}
              aria-hidden="true"
            />
          );
        })}
    </>
  );
}
