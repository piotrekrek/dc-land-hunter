import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Ruler } from "./Ruler";

describe("Ruler", () => {
  it("draws the solid part to the score and the hatched part to the potential", () => {
    render(<Ruler score={38} upside={61} />);
    expect(screen.getByRole("img", { name: "Linijka wyniku: 38, potencjał 61, progi 40 i 70" })).toBeInTheDocument();
    expect(screen.getByTestId("ruler-solid")).toHaveStyle({ width: "38%" });
    expect(screen.getByTestId("ruler-hatch")).toHaveStyle({ left: "38%", width: "23%" });
    expect(screen.getByLabelText("Wynik 38 na 100")).toBeInTheDocument();
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(screen.getByText("70")).toBeInTheDocument();
  });

  it("never draws a potential below the score, and keeps both inside 0 to 100", () => {
    render(<Ruler score={120} upside={50} />);
    expect(screen.getByTestId("ruler-solid")).toHaveStyle({ width: "100%" });
    expect(screen.getByTestId("ruler-hatch")).toHaveStyle({ width: "0%" });
  });

  it("rounds the numbers the Polish way", () => {
    render(<Ruler score={81.4} upside={83.6} />);
    expect(screen.getByRole("img", { name: /Linijka wyniku: 81, potencjał 84/ })).toBeInTheDocument();
  });
});
