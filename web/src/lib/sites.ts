import type { components } from "@/api/schema";

export type SiteSummary = components["schemas"]["SiteSummary"];
export type SiteDetail = components["schemas"]["SiteDetail"];
/** A site the user picked in the list; the map flies to it. A new object per click, so picking it again re-centres. */
export interface MapFocus {
  siteId: string;
  lon: number;
  lat: number;
}

export type GridBadge = SiteSummary["grid_badge"];
export type Confidence = components["schemas"]["FactOut"]["confidence"];

export const SORT_OPTIONS = [
  { value: "score", label: "Wynik" },
  { value: "upside", label: "Potencjał" },
  { value: "completeness", label: "Kompletność danych" },
  { value: "area", label: "Powierzchnia" },
  { value: "mw", label: "Szacowana moc" },
] as const;
export type SortKey = (typeof SORT_OPTIONS)[number]["value"];

/** The grid badges a filter can pick; "Brak danych" is what remains, so it is not offered. */
export const GRID_BADGE_OPTIONS = ["Opublikowana moc", "Bliskość sieci"] as const satisfies readonly GridBadge[];

/** Brief 2.2: the target is 60 MW and 10 ha. The filters start there; the user can lower them. */
export const MW_OPTIONS = [0, 60, 100, 150] as const;
export const HA_OPTIONS = [0, 10, 20, 30] as const;

export interface SiteFilters {
  sort: SortKey;
  minMw: number;
  minHa: number;
  includeExcluded: boolean;
  /** TERYT code of the voivodeship (2 digits) and of the powiat (4 digits); "" means any. A powiat lies in one voivodeship. */
  voivodeship: string;
  powiat: string;
  /** "" means any badge. */
  gridBadge: "" | (typeof GRID_BADGE_OPTIONS)[number];
  /** Only sites flagged "Duży kampus" / "Do podziału". */
  largeCampus: boolean;
  splitSuggested: boolean;
}

export const DEFAULT_FILTERS: SiteFilters = {
  sort: "score",
  minMw: 60,
  minHa: 10,
  includeExcluded: false,
  voivodeship: "",
  powiat: "",
  gridBadge: "",
  largeCampus: false,
  splitSuggested: false,
};

/** A TERYT code from the URL, kept only when it has the right shape: anything else would only earn a 422. */
const terytOption = (value: string | null, digits: number): string =>
  value !== null && new RegExp(`^\\d{${digits}}$`).test(value) ? value : "";

/** A numeric URL value, kept only when it is one of the offered options. */
const numberOption = (options: readonly number[], value: string | null, fallback: number): number => {
  const n = Number(value);
  return value !== null && options.includes(n) ? n : fallback;
};

/** Reads filters from the URL, so a filtered list can be bookmarked and shared (brief 8.1). */
export function parseFilters(params: URLSearchParams): SiteFilters {
  const sort = SORT_OPTIONS.map((o) => o.value).find((v) => v === params.get("sort")) ?? DEFAULT_FILTERS.sort;
  const powiat = terytOption(params.get("powiat"), 4);
  return {
    sort,
    minMw: numberOption(MW_OPTIONS, params.get("minMw"), DEFAULT_FILTERS.minMw),
    minHa: numberOption(HA_OPTIONS, params.get("minHa"), DEFAULT_FILTERS.minHa),
    includeExcluded: params.get("excluded") === "1",
    // The powiat code starts with its voivodeship's, so a powiat in the URL also fixes the voivodeship.
    voivodeship: powiat ? powiat.slice(0, 2) : terytOption(params.get("woj"), 2),
    powiat,
    gridBadge: GRID_BADGE_OPTIONS.find((b) => b === params.get("siec")) ?? "",
    largeCampus: params.get("kampus") === "1",
    splitSuggested: params.get("podzial") === "1",
  };
}

/** Writes only the values that differ from the defaults, so the default URL stays clean. */
export function toParams(filters: SiteFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.sort !== DEFAULT_FILTERS.sort) params.set("sort", filters.sort);
  if (filters.minMw !== DEFAULT_FILTERS.minMw) params.set("minMw", String(filters.minMw));
  if (filters.minHa !== DEFAULT_FILTERS.minHa) params.set("minHa", String(filters.minHa));
  if (filters.includeExcluded) params.set("excluded", "1");
  if (filters.voivodeship) params.set("woj", filters.voivodeship);
  if (filters.powiat) params.set("powiat", filters.powiat);
  if (filters.gridBadge) params.set("siec", filters.gridBadge);
  if (filters.largeCampus) params.set("kampus", "1");
  if (filters.splitSuggested) params.set("podzial", "1");
  return params;
}

/** True when every field, sort included, has its default value. */
export const isDefaultFilters = (filters: SiteFilters): boolean =>
  (Object.keys(DEFAULT_FILTERS) as (keyof SiteFilters)[]).every((key) => filters[key] === DEFAULT_FILTERS[key]);

/**
 * The filters as a short Polish line above the list. Sort is not a filter, so it is left out.
 * The default line names only the MW filter: at the default 6 MW/ha, 60 MW and 10 ha select the same sites.
 */
export function describeFilters(filters: SiteFilters): string {
  const filtersAtDefault =
    filters.minMw === DEFAULT_FILTERS.minMw && filters.minHa === DEFAULT_FILTERS.minHa && !filters.includeExcluded && !filters.voivodeship && !filters.gridBadge && !filters.largeCampus && !filters.splitSuggested;
  if (filtersAtDefault) return `Filtry domyślne: od ${DEFAULT_FILTERS.minMw} MW`;
  const parts = [filters.minMw > 0 ? `od ${filters.minMw} MW` : "moc dowolna"];
  if (filters.minHa > 0) parts.push(`powierzchnia od ${filters.minHa} ha`);
  if (filters.includeExcluded) parts.push("z wykluczonymi");
  if (filters.powiat) parts.push("wybrany powiat");
  else if (filters.voivodeship) parts.push("wybrane województwo");
  if (filters.gridBadge) parts.push(`sieć: ${filters.gridBadge.toLowerCase()}`);
  if (filters.largeCampus) parts.push("duży kampus");
  if (filters.splitSuggested) parts.push("do podziału");
  return `Filtry: ${parts.join(", ")}`;
}

/** "gm. X, pow. Y" for a list row; the card adds the voivodeship. Names the API could not resolve are left out. */
export function placeLine(site: Pick<SiteSummary, "gmina" | "powiat" | "voivodeship">, withVoivodeship = false): string {
  const parts = [
    site.gmina && `gm. ${site.gmina}`,
    site.powiat && `pow. ${site.powiat.replace(/^powiat\s+/i, "")}`,
    withVoivodeship && site.voivodeship && `woj. ${site.voivodeship}`,
  ];
  return parts.filter(Boolean).join(", ");
}

/** Geoportal's parcel finder: opens the national map on the parcel (TERYT parcel id). */
export const geoportalParcelUrl = (parcelId: string) =>
  `https://mapy.geoportal.gov.pl/imap/Imgp_2.html?identifyParcel=${encodeURIComponent(parcelId)}`;

/** Keeps the first row of each site: a site that moved between two pages must show once. */
export function uniqueBySiteId<T extends { site_id: string }>(items: readonly T[]): T[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    if (seen.has(item.site_id)) return false;
    seen.add(item.site_id);
    return true;
  });
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

/** The marks of the Protokół: ✓ checked, ≈ estimate, ? no data, ✕ condition not met. */
export type MarkKind = "ok" | "est" | "unk" | "bad";

export function markForConfidence(confidence: Confidence): MarkKind {
  return confidence === "verified" ? "ok" : confidence === "estimated" ? "est" : "unk";
}

/**
 * Score criteria that no data feeds in this version (docs/SOURCES.md): the UKE telecom nodes cannot be downloaded, the
 * environmental decisions and public sellers are manual-only, the PSE planned load stations are not loaded yet. They
 * always count as 0 points, so the Protokół folds them into one line. Same keys as `NO_DATA_YET` in parcel_check.py.
 */
export const NOT_YET_KEYS: ReadonlySet<string> = new Set(["conn_telecom", "bonus_env", "bonus_seller"]);

