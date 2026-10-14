import type { components } from "@/api/schema";

export type SiteSummary = components["schemas"]["SiteSummary"];
export type SiteDetail = components["schemas"]["SiteDetail"];
/** A site the user picked in the list; the map flies to it. A new object per click, so picking it again re-centres. */
export interface MapFocus {
  siteId: string;
  lon: number;
  lat: number;
}

export type Confidence = components["schemas"]["FactOut"]["confidence"];

export const SORT_OPTIONS = [
  { value: "score", label: "Wynik" },
  { value: "upside", label: "Potencjał" },
  { value: "completeness", label: "Kompletność danych" },
  { value: "area", label: "Powierzchnia" },
  { value: "mw", label: "Szacowana moc" },
] as const;
export type SortKey = (typeof SORT_OPTIONS)[number]["value"];

/** Brief 2.2: the target is 60 MW and 10 ha. The filters start there; the user can lower them. */
export const MW_OPTIONS = [0, 60, 100, 150] as const;
export const HA_OPTIONS = [0, 10, 20, 30] as const;

export interface SiteFilters {
  sort: SortKey;
  minMw: number;
  minHa: number;
  includeExcluded: boolean;
}

export const DEFAULT_FILTERS: SiteFilters = { sort: "score", minMw: 60, minHa: 10, includeExcluded: false };

/** A numeric URL value, kept only when it is one of the offered options. */
const numberOption = (options: readonly number[], value: string | null, fallback: number): number => {
  const n = Number(value);
  return value !== null && options.includes(n) ? n : fallback;
};

/** Reads filters from the URL, so a filtered list can be bookmarked and shared (brief 8.1). */
export function parseFilters(params: URLSearchParams): SiteFilters {
  const sort = SORT_OPTIONS.map((o) => o.value).find((v) => v === params.get("sort")) ?? DEFAULT_FILTERS.sort;
  return {
    sort,
    minMw: numberOption(MW_OPTIONS, params.get("minMw"), DEFAULT_FILTERS.minMw),
    minHa: numberOption(HA_OPTIONS, params.get("minHa"), DEFAULT_FILTERS.minHa),
    includeExcluded: params.get("excluded") === "1",
  };
}

/** Writes only the values that differ from the defaults, so the default URL stays clean. */
export function toParams(filters: SiteFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.sort !== DEFAULT_FILTERS.sort) params.set("sort", filters.sort);
  if (filters.minMw !== DEFAULT_FILTERS.minMw) params.set("minMw", String(filters.minMw));
  if (filters.minHa !== DEFAULT_FILTERS.minHa) params.set("minHa", String(filters.minHa));
  if (filters.includeExcluded) params.set("excluded", "1");
  return params;
}

export const CONFIDENCE_LABEL: Record<Confidence, string> = {
  verified: "Zweryfikowane",
  estimated: "Szacunek",
  missing: "Brak danych",
};

export const CATEGORY_LABEL: Record<string, string> = {
  grid: "Sieć energetyczna",
  zoning: "Plan zagospodarowania",
  land: "Działka",
  connectivity: "Połączenia",
  bonus: "Bonusy",
  penalty: "Kary",
};
