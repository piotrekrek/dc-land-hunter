import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SiteDetail } from "@/lib/sites";

import { Protokol } from "./Protokol";

const site: Pick<SiteDetail, "items" | "categories" | "exclusions"> = {
  exclusions: [],
  categories: [
    { category: "zoning", points: 5, max_points: 25, upside_points: 25 },
    { category: "grid", points: 27, max_points: 45, upside_points: 40 },
  ],
  items: [
    { key: "plan", category: "zoning", label_pl: "Plan miejscowy", points: 0, max_points: 25, upside_points: 25, confidence: "missing", source_id: "ru", as_of: null, explanation_pl: "Brak planu w rejestrze.", value: null },
    { key: "line110", category: "grid", label_pl: "Linia 110 kV", points: 27, max_points: 27, upside_points: 27, confidence: "estimated", source_id: "osm_power", as_of: "2026-10-10T06:00:00+00:00", explanation_pl: "Linia 110 kV w odległości 0,6 km.", value: 0.6 },
    { key: "penalty_x", category: "penalty", label_pl: "Kara", points: -4.5, max_points: 0, upside_points: 0, confidence: "verified", source_id: null, as_of: null, explanation_pl: "Las w pobliżu.", value: null },
  ],
};

describe("Protokol", () => {
  it("lists the groups in the fixed order, not in the order of the data", () => {
    render(<Protokol site={site} />);
    const headings = screen.getAllByRole("heading").map((h) => h.textContent);
    expect(headings).toEqual(["Sieć energetyczna", "Plan zagospodarowania", "Kary"]);
  });

  it("shows the finding, a mark, the source with its date, and the points", () => {
    render(<Protokol site={site} />);
    const grid = screen.getAllByTestId("breakdown-group")[0]!;
    expect(within(grid).getByText("27 z 45 pkt")).toBeInTheDocument();
    expect(within(grid).getByText("Linia 110 kV w odległości 0,6 km.")).toBeInTheDocument();
    expect(within(grid).getByText("+27 / 27")).toBeInTheDocument();
    expect(within(grid).getByText(/źródło: osm_power, stan na 10\.10\.2026/)).toBeInTheDocument();
    expect(within(grid).getByRole("img", { name: "szacunek" })).toBeInTheDocument();
  });

  it("says plainly that missing data is missing, not bad", () => {
    render(<Protokol site={site} />);
    const zoning = screen.getAllByTestId("breakdown-group")[1]!;
    expect(within(zoning).getByText("Brak danych")).toBeInTheDocument();
    expect(within(zoning).getByRole("img", { name: "brak danych" })).toBeInTheDocument();
  });

  it("puts failed conditions first, with a ✕ and the penalty", () => {
    render(
      <Protokol
        site={{
          ...site,
          exclusions: [
            { key: "area", reason_pl: "Powierzchnia poniżej 10 ha.", mode: "exclude", penalty_points: 0 },
            { key: "forest", reason_pl: "Las na 35% działki.", mode: "penalty", penalty_points: 12 },
          ],
        }}
      />,
    );
    const gates = screen.getByTestId("gates-group");
    expect(gates.compareDocumentPosition(screen.getAllByTestId("breakdown-group")[0]!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(gates).getByText("niespełnione: 2")).toBeInTheDocument();
    expect(within(gates).getByText("Wyklucza lokalizację")).toBeInTheDocument();
    expect(within(gates).getByText("Kara punktowa")).toBeInTheDocument();
    expect(within(gates).getByText("−12 pkt")).toBeInTheDocument();
    expect(within(gates).getAllByRole("img", { name: "nie spełnia warunku" })).toHaveLength(2);
  });

  it("has a legend for the marks and no gates group when nothing failed", () => {
    render(<Protokol site={site} />);
    expect(screen.getByLabelText("Znaczenie znaków")).toBeInTheDocument();
    expect(screen.queryByTestId("gates-group")).not.toBeInTheDocument();
  });
});
