import { describe, expect, it } from "vitest";

import type { CoverageArea } from "@/lib/coverage";
import { coverageBand, coverageBandLabel, coverageCollection } from "@/lib/coverage";

const area = (over: Partial<CoverageArea>): CoverageArea => ({
  teryt: "1421011",
  name: "Piaseczno",
  sites: 4,
  with_data: 3,
  total: 4,
  share: 0.75,
  by_item: { zoning: 0.5, pen_soil_iii: 0, bonus_psi: 1, grid_headroom: 0.25 },
  geometry: null,
  ...over,
});

describe("coverage bands", () => {
  it("puts each share in the band it starts, and 100% in the last band", () => {
    expect([0, 0.19, 0.2, 0.59, 0.6, 0.99, 1].map(coverageBand)).toEqual([0, 0, 1, 2, 3, 4, 4]);
  });

  it("clamps shares outside 0–1 instead of indexing past the palette", () => {
    expect(coverageBand(-0.1)).toBe(0);
    expect(coverageBand(1.2)).toBe(4);
  });

  it("labels the bands with whole percentages that do not overlap", () => {
    expect([0, 1, 4].map(coverageBandLabel)).toEqual(["0–19%", "20–39%", "80–100%"]);
  });
});

describe("coverageCollection", () => {
  it("keeps the areas that have a shape, with their share for the map", () => {
    const shape = { type: "Polygon" as const, coordinates: [[[20, 52], [21, 52], [21, 53], [20, 52]]] };
    const collection = coverageCollection([area({ geometry: shape }), area({ teryt: "1425011", geometry: null })]);
    expect(collection.features).toHaveLength(1);
    expect(collection.features[0]).toMatchObject({
      type: "Feature",
      geometry: shape,
      properties: { teryt: "1421011", name: "Piaseczno", sites: 4, with_data: 3, total: 4, share: 0.75 },
    });
  });

  it("is an empty collection when there is nothing to draw", () => {
    expect(coverageCollection([])).toEqual({ type: "FeatureCollection", features: [] });
  });
});
