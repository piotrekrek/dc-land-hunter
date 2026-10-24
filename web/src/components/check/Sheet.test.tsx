import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { sheet } from "@/test/checkFixtures";

import { Sheet } from "./Sheet";

describe("Sheet", () => {
  it("draws the parcel and the layers that have paths, north up", () => {
    const { container } = render(<Sheet sheet={sheet} />);
    expect(container.querySelector('[data-layer="parcel"]')).toBeInTheDocument();
    const flood = container.querySelector('[data-layer="flood"]')!;
    // Local metres, north up: the SVG flips the vertical axis, so y = 100 is drawn at -100.
    expect(flood.getAttribute("d")).toContain("L200 -100");
    expect(container.querySelector('[data-layer="housing"]')).toBeInTheDocument();
    expect(container.querySelector('[data-layer="forest"]')).not.toBeInTheDocument(); // too big to draw
  });

  it("describes the drawing in words, so it is never the only carrier of the information", () => {
    render(<Sheet sheet={sheet} />);
    const label = screen.getByRole("img").getAttribute("aria-label")!;
    expect(label).toContain("Strefa zalewowa 1%");
    expect(label).toContain("Budynki mieszkalne");
    expect(label).not.toContain("Las"); // a layer that is not drawn is not claimed
  });

  it("has a toggle per layer; switching one off removes it from the drawing", async () => {
    const user = userEvent.setup();
    const { container } = render(<Sheet sheet={sheet} />);
    const toggles = within(screen.getByRole("group", { name: "Warstwy rysunku" }));
    const flood = toggles.getByRole("button", { name: /Strefa zalewowa 1%/ });
    expect(flood).toHaveAttribute("aria-pressed", "true");
    await user.click(flood);
    expect(flood).toHaveAttribute("aria-pressed", "false");
    expect(container.querySelector('[data-layer="flood"]')).not.toBeInTheDocument();
    await user.click(flood);
    expect(container.querySelector('[data-layer="flood"]')).toBeInTheDocument();
  });

  it("lists a layer that was too big as not drawn, and cannot be toggled", () => {
    render(<Sheet sheet={sheet} />);
    const forest = screen.getByRole("button", { name: /Las/ });
    expect(forest).toBeDisabled();
    expect(forest).toHaveTextContent("nie narysowana");
  });

  it("does not offer a layer that is simply empty here", () => {
    render(<Sheet sheet={sheet} />);
    expect(screen.queryByRole("button", { name: /Obszar górniczy/ })).not.toBeInTheDocument();
  });

  it("draws a scale bar that is a round length for the width of the sheet", () => {
    render(<Sheet sheet={sheet} />);
    expect(screen.getByTestId("scale-label")).toHaveTextContent("250 m"); // 1100 m wide: a quarter is 275 m
  });
});
