// The logo used to be a plain <a> to the marketing site everywhere, so a signed-in person clicking it
// was thrown out of the app onto the landing page. In the case bar it was also an <a> nested inside a
// <Link>, which is invalid HTML.
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";
import { Logo } from "./Logo";

afterEach(() => vi.unstubAllEnvs());

const at = (ui: React.ReactNode) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe("the logo", () => {
  test("goes to the signed-in home by default, not the marketing site", () => {
    at(<Logo />);
    expect(screen.getByRole("link", { name: "Avodeck home page" })).toHaveAttribute("href", "/");
  });

  test("goes to the marketing site only where asked", () => {
    vi.stubEnv("VITE_SITE_URL", "https://example.test");
    at(<Logo to="site" />);
    expect(screen.getByRole("link", { name: "Avodeck home page" })).toHaveAttribute("href", "https://example.test");
  });

  test("draws no link at all when the bar around it is the link", () => {
    at(<Logo to="none" />);
    expect(screen.queryByRole("link")).toBeNull();
  });
});
