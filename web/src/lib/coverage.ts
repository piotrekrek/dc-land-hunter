import type { GeoJSONSourceSpecification } from "maplibre-gl";

import type { components } from "@/api/schema";

export type CoverageLevel = "wojewodztwo" | "powiat" | "gmina";
/** "all" is every score item together; the rest are the items the API tracks. */
export type CoverageItem = "all" | "zoning" | "pen_soil_iii" | "bonus_psi" | "grid_headroom";
export type CoverageArea = components["schemas"]["CoverageArea"];
export type CoverageCollection = Extract<GeoJSONSourceSpecification["data"], { type: "FeatureCollection" }>;
type CoverageFeature = CoverageCollection["features"][number];

export const COVERAGE_LEVEL_LABELS: Record<CoverageLevel, string> = {
  wojewodztwo: "Województwo",
  powiat: "Powiat",
  gmina: "Gmina",
};

export const COVERAGE_ITEM_LABELS: Record<CoverageItem, string> = {
  all: "Wszystkie wskaźniki",
  zoning: "Przeznaczenie terenu",
  pen_soil_iii: "Gleby klasy III",
  bonus_psi: "Pomoc regionalna (PSI)",
  grid_headroom: "Opublikowana moc GPZ",
};

/** Items in the order the selector and the table show them. */
export const COVERAGE_ITEMS: CoverageItem[] = ["all", "zoning", "pen_soil_iii", "bonus_psi", "grid_headroom"];

/** Share bands, lower bound first. The last band runs to 100%. */
export const COVERAGE_BAND_EDGES = [0, 0.2, 0.4, 0.6, 0.8] as const;

/** Band index of a share in [0, 1]; a share of exactly 1 belongs to the last band. */
export function coverageBand(share: number): number {
  return Math.min(COVERAGE_BAND_EDGES.length - 1, Math.max(0, Math.floor(share * COVERAGE_BAND_EDGES.length)));
}

export function coverageBandLabel(band: number): string {
  const low = Math.round(COVERAGE_BAND_EDGES[band]! * 100);
  const high = band + 1 < COVERAGE_BAND_EDGES.length ? Math.round(COVERAGE_BAND_EDGES[band + 1]! * 100) - 1 : 100;
  return `${low}–${high}%`;
}

/** Feature properties the map styles and labels read. `share` is the 0–1 value the colour is chosen from. */
export interface CoverageProperties {
  teryt: string;
  name: string;
  sites: number;
  with_data: number;
  total: number;
  share: number;
}

/** Areas that have a shape, as a GeoJSON collection for the map. Areas without one are left out. */
export function coverageCollection(areas: CoverageArea[]): CoverageCollection {
  const features = areas.flatMap((area): CoverageFeature[] => {
    if (!area.geometry) return [];
    const properties: CoverageProperties = {
      teryt: area.teryt,
      name: area.name,
      sites: area.sites,
      with_data: area.with_data,
      total: area.total,
      share: area.share,
    };
    return [{ type: "Feature", geometry: area.geometry as CoverageFeature["geometry"], properties }];
  });
  return { type: "FeatureCollection", features };
}
