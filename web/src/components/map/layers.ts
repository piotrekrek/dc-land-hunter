import type {
  ExpressionSpecification,
  FilterSpecification,
  GeoJSONSource,
  LayerSpecification,
  Map as MapLibreMap,
  SourceSpecification,
} from "maplibre-gl";

import { COVERAGE_BAND_EDGES, coverageBandLabel, type CoverageCollection } from "@/lib/coverage";
import type { MapFocus } from "@/lib/sites";

import type { TileProps } from "./popup";

/** Grid layers served by Martin from the `tiles` schema (brief 8.1: grid by voltage, substations, AOI). */
export interface LayerGroup {
  id: string;
  label: string;
  defaultOn: boolean;
  layerIds: string[];
  /** Legend swatch, for layers drawn in one colour. */
  color?: string;
  /** Legend entries, for layers drawn in several colours. */
  legend?: { color: string; label: string }[];
}

const tileUrl = (name: string) => `${globalThis.location?.origin ?? ""}/tiles/${name}/{z}/{x}/{y}`;

/** Single sites are drawn from this zoom; below it they are too small, so counted bubbles stand in for them. */
export const SITE_MIN_ZOOM = 8;

const SOURCES: Record<string, SourceSpecification> = {
  aoi: { type: "vector", tiles: [tileUrl("aoi")], maxzoom: 12 },
  lines: { type: "vector", tiles: [tileUrl("lines")], minzoom: 4 },
  substations: { type: "vector", tiles: [tileUrl("substations")], minzoom: 5 },
  sites: { type: "vector", tiles: [tileUrl("sites")], minzoom: SITE_MIN_ZOOM },
  site_clusters: { type: "vector", tiles: [tileUrl("site_clusters")], maxzoom: SITE_MIN_ZOOM - 1 },
};

/** Site colours by tone. Hex, because MapLibre cannot read the oklch tokens in index.css (ok, warn, bad, muted-foreground). */
export const SITE_COLORS = { ok: "#187c49", warn: "#814d00", bad: "#ba3630", muted: "#555c63" } as const;
export type SiteTone = keyof typeof SITE_COLORS;
/** The same thresholds as the score badge in the list (ScoreBadge.tsx). */
export const SITE_SCORE_OK = 70;
export const SITE_SCORE_WARN = 40;

/** Which colour a site gets: excluded sites are grey whatever their score; the rest follow the list's badge. */
export function siteTone(score: number, excluded: boolean): SiteTone {
  if (excluded) return "muted";
  if (score >= SITE_SCORE_OK) return "ok";
  if (score >= SITE_SCORE_WARN) return "warn";
  return "bad";
}

// Tiles may carry the boolean as true, "true" or 1 (see popup.ts), so match all three.
const SITE_TONE_EXPRESSION: ExpressionSpecification = [
  "case",
  ["in", ["to-string", ["get", "excluded"]], ["literal", ["true", "1"]]],
  SITE_COLORS.muted,
  [
    "step",
    ["to-number", ["get", "score"]],
    SITE_COLORS.bad,
    SITE_SCORE_WARN,
    SITE_COLORS.warn,
    SITE_SCORE_OK,
    SITE_COLORS.ok,
  ],
];

/** Coverage bands, from low to high coverage: a sequential blue-green scale that stays readable on both basemaps. */
export const COVERAGE_COLORS = ["#ffffcc", "#c7e9b4", "#7fcdbb", "#41b6c4", "#225ea8"] as const;
export const COVERAGE_SOURCE_ID = "coverage";
export const COVERAGE_FILL_ID = "coverage-fill";
export const COVERAGE_LINE_ID = "coverage-line";
const EMPTY_COVERAGE: CoverageCollection = { type: "FeatureCollection", features: [] };

/** Step over the 0–1 share: one colour per band. Shares arrive as numbers, so no `to-number` is needed. */
const COVERAGE_COLOR_EXPRESSION: ExpressionSpecification = [
  "step",
  ["get", "share"],
  COVERAGE_COLORS[0],
  ...COVERAGE_BAND_EDGES.slice(1).flatMap((edge, i) => [edge, COVERAGE_COLORS[i + 1]!]),
];

export const SITE_FILL_ID = "sites-fill";
/** The selected site's outline, drawn on top; its filter picks one site by `site_id`. */
export const SITE_HIGHLIGHT_ID = "sites-selected";
export const SITE_CLUSTER_ID = "site-clusters";
export const SITE_CLUSTER_LABEL_ID = "site-clusters-count";

/**
 * A bubble takes the colour of the best score among its candidates (excluded sites do not count; the tile omits
 * `best` when every site in the bubble is excluded, so a missing value reads as -1 and goes grey).
 */
const CLUSTER_TONE_EXPRESSION: ExpressionSpecification = [
  "step",
  ["to-number", ["get", "best"], -1],
  SITE_COLORS.muted,
  0,
  SITE_COLORS.bad,
  SITE_SCORE_WARN,
  SITE_COLORS.warn,
  SITE_SCORE_OK,
  SITE_COLORS.ok,
];

/** Where a click on a bubble zooms to: two levels in, but no further than just past the zoom that shows sites. */
export function clusterZoomTarget(zoom: number): number {
  return Math.min(zoom + 2, SITE_MIN_ZOOM + 1);
}
/** Stations whose voltage OSM does not give: thousands of them, so they get their own checkbox. */
export const UNKNOWN_STATIONS_ID = "substations-unknown-circle";
export const STATION_LAYER_IDS: string[] = ["substations-circle", UNKNOWN_STATIONS_ID];

export function siteHighlightFilter(siteId: string | null): FilterSpecification {
  // Site ids are never empty, so a null selection matches nothing.
  return ["==", ["get", "site_id"], siteId ?? ""];
}

/** The focus a click on a site polygon selects: the same shape as picking its row in the list. */
export function siteFocus(props: TileProps, at: { lng: number; lat: number }): MapFocus | null {
  return typeof props.site_id === "string" && props.site_id ? { siteId: props.site_id, lon: at.lng, lat: at.lat } : null;
}

/** Ids of the sources served by Martin; any other source error belongs to the basemap. */
export const OWN_SOURCES: ReadonlySet<string> = new Set(Object.keys(SOURCES));

// Voltage colours: distinct in hue and lightness so they stay apart for common colour-vision deficiencies.
export const VOLTAGE_COLORS = { v110: "#2b6cb0", v220: "#d97706", v400: "#b91c1c" } as const;

/**
 * One layer per line voltage, so each can be switched on its own. Bounds are in volts, as OSM tags them.
 * The 400 kV band starts at 380 kV, the value OSM uses for part of the 400 kV network; 450 kV lines land there too.
 */
export const LINE_BANDS = [
  { id: "lines-110", label: "110 kV", color: VOLTAGE_COLORS.v110, min: 110_000, max: 220_000 },
  { id: "lines-220", label: "220 kV", color: VOLTAGE_COLORS.v220, min: 220_000, max: 380_000 },
  { id: "lines-400", label: "400 kV", color: VOLTAGE_COLORS.v400, min: 380_000, max: Number.MAX_SAFE_INTEGER },
] as const;

export const LINE_LAYER_IDS: string[] = LINE_BANDS.map((band) => band.id);

const LINE_LAYERS: LayerSpecification[] = LINE_BANDS.map(
  (band): LayerSpecification => ({
    id: band.id,
    type: "line",
    source: "lines",
    "source-layer": "lines",
    filter: [
      "all",
      [">=", ["get", "voltage"], band.min],
      ["<", ["get", "voltage"], band.max],
    ] satisfies FilterSpecification,
    paint: {
      "line-color": band.color,
      "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.6, 12, 2.4],
    },
  }),
);

const COVERAGE_LAYERS: LayerSpecification[] = [
  {
    id: COVERAGE_FILL_ID,
    type: "fill",
    source: COVERAGE_SOURCE_ID,
    paint: { "fill-color": COVERAGE_COLOR_EXPRESSION, "fill-opacity": 0.55 },
  },
  {
    id: COVERAGE_LINE_ID,
    type: "line",
    source: COVERAGE_SOURCE_ID,
    paint: { "line-color": COVERAGE_COLOR_EXPRESSION, "line-width": 1 },
  },
];

export const LAYERS: LayerSpecification[] = [
  {
    id: "aoi-fill",
    type: "fill",
    source: "aoi",
    "source-layer": "aoi",
    paint: { "fill-color": "#1f5f7a", "fill-opacity": 0.06 },
  },
  {
    id: "aoi-line",
    type: "line",
    source: "aoi",
    "source-layer": "aoi",
    paint: { "line-color": "#1f5f7a", "line-width": 1.5, "line-dasharray": [3, 2] },
  },
  // Coverage sits under the sites and lines, so the candidates stay clickable on top of it.
  ...COVERAGE_LAYERS,
  {
    id: SITE_FILL_ID,
    type: "fill",
    source: "sites",
    "source-layer": "sites",
    paint: { "fill-color": SITE_TONE_EXPRESSION, "fill-opacity": 0.45 },
  },
  {
    id: "sites-line",
    type: "line",
    source: "sites",
    "source-layer": "sites",
    paint: { "line-color": SITE_TONE_EXPRESSION, "line-width": 1.5 },
  },
  ...LINE_LAYERS,
  {
    id: "substations-circle",
    type: "circle",
    source: "substations",
    "source-layer": "substations",
    filter: ["!=", ["get", "cls"], "UNKNOWN_VOLTAGE"] satisfies FilterSpecification,
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 5, 2, 12, 6],
      "circle-color": [
        "match",
        ["get", "cls"],
        "NODE_220_400",
        VOLTAGE_COLORS.v400,
        "GPZ_110_SN",
        VOLTAGE_COLORS.v110,
        "#6b7280",
      ],
      "circle-stroke-color": "#111827",
      "circle-stroke-width": 1,
    },
  },
  {
    id: UNKNOWN_STATIONS_ID,
    type: "circle",
    source: "substations",
    "source-layer": "substations",
    filter: ["==", ["get", "cls"], "UNKNOWN_VOLTAGE"] satisfies FilterSpecification,
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 5, 2, 12, 6],
      "circle-color": "#ffffff",
      "circle-stroke-color": "#111827",
      "circle-stroke-width": 1,
    },
  },
  {
    id: SITE_CLUSTER_ID,
    type: "circle",
    source: "site_clusters",
    "source-layer": "site_clusters",
    maxzoom: SITE_MIN_ZOOM,
    paint: {
      "circle-color": CLUSTER_TONE_EXPRESSION,
      "circle-opacity": 0.9,
      "circle-radius": ["interpolate", ["linear"], ["sqrt", ["get", "n"]], 1, 12, 70, 34],
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 2,
    },
  },
  {
    id: SITE_CLUSTER_LABEL_ID,
    type: "symbol",
    source: "site_clusters",
    "source-layer": "site_clusters",
    maxzoom: SITE_MIN_ZOOM,
    layout: {
      "text-field": ["to-string", ["get", "n"]],
      // Both OpenFreeMap styles ship this font; the fallback style has no glyphs, so there the bubbles have no number.
      "text-font": ["Noto Sans Regular"],
      "text-size": 13,
      "text-allow-overlap": true,
      "text-ignore-placement": true,
    },
    paint: { "text-color": "#ffffff" },
  },
  {
    id: SITE_HIGHLIGHT_ID,
    type: "line",
    source: "sites",
    "source-layer": "sites",
    filter: siteHighlightFilter(null),
    paint: { "line-color": "#ffffff", "line-width": 3 },
  },
];

const SITE_LEGEND: NonNullable<LayerGroup["legend"]> = [
  { color: SITE_COLORS.ok, label: `Wynik ${SITE_SCORE_OK} lub więcej` },
  { color: SITE_COLORS.warn, label: `Wynik ${SITE_SCORE_WARN}–${SITE_SCORE_OK - 1}` },
  { color: SITE_COLORS.bad, label: `Wynik poniżej ${SITE_SCORE_WARN}` },
  { color: SITE_COLORS.muted, label: "Wykluczone" },
];

const COVERAGE_LEGEND: NonNullable<LayerGroup["legend"]> = COVERAGE_COLORS.map((color, band) => ({
  color,
  label: coverageBandLabel(band),
}));

export const LAYER_GROUPS: LayerGroup[] = [
  { id: "aoi", label: "Obszar poszukiwań (AOI)", defaultOn: true, layerIds: ["aoi-fill", "aoi-line"] },
  {
    id: "sites",
    label: "Kandydaci (działki)",
    defaultOn: true,
    layerIds: [SITE_FILL_ID, "sites-line", SITE_HIGHLIGHT_ID, SITE_CLUSTER_ID, SITE_CLUSTER_LABEL_ID],
    legend: SITE_LEGEND,
  },
  {
    id: "coverage",
    label: "Pokrycie danych",
    defaultOn: false,
    layerIds: [COVERAGE_FILL_ID, COVERAGE_LINE_ID],
    legend: COVERAGE_LEGEND,
  },
  ...LINE_BANDS.map((band) => ({
    id: band.id,
    label: `Linie ${band.label}`,
    defaultOn: true,
    layerIds: [band.id],
    color: band.color,
  })),
  { id: "substations", label: "Stacje (GPZ, węzły)", defaultOn: true, layerIds: ["substations-circle"] },
  {
    id: "substations-unknown",
    label: "Stacje o nieznanym napięciu",
    defaultOn: false,
    layerIds: [UNKNOWN_STATIONS_ID],
    color: "#ffffff",
  },
];

/**
 * The dark basemap paints buildings (fill rgb(10,10,10)) almost the colour of the ground, so they are invisible.
 * Lift them to a readable grey. No-op when the style has no `building` layer (e.g. the fallback style).
 */
export function brightenBuildings(map: MapLibreMap) {
  if (!map.getLayer("building")) return;
  map.setPaintProperty("building", "fill-color", "#2b313a");
  map.setPaintProperty("building", "fill-outline-color", "#5b6573");
}

/** (Re)adds sources and layers; call after every `style.load` because `setStyle` removes them. */
export function addGridLayers(
  map: MapLibreMap,
  visible: Record<string, boolean>,
  darkBasemap = true,
  selectedSiteId: string | null = null,
  coverage: CoverageCollection = EMPTY_COVERAGE,
) {
  // Only the dark basemap needs lighter buildings; the light style already draws them light.
  if (darkBasemap) brightenBuildings(map);
  for (const [id, spec] of Object.entries(SOURCES)) {
    if (!map.getSource(id)) map.addSource(id, spec);
  }
  if (!map.getSource(COVERAGE_SOURCE_ID)) map.addSource(COVERAGE_SOURCE_ID, { type: "geojson", data: coverage });
  for (const layer of LAYERS) {
    if (!map.getLayer(layer.id)) map.addLayer(layer);
  }
  for (const group of LAYER_GROUPS) setGroupVisibility(map, group, visible[group.id] ?? group.defaultOn);
  setSiteHighlight(map, selectedSiteId);
}

/** Replaces the coverage shapes; the source may not exist yet (before the style loads), then nothing happens. */
export function setCoverageData(map: MapLibreMap, collection: CoverageCollection) {
  (map.getSource(COVERAGE_SOURCE_ID) as GeoJSONSource | undefined)?.setData(collection);
}

/** Outlines the selected site's polygon; null clears the outline. */
export function setSiteHighlight(map: MapLibreMap, siteId: string | null) {
  if (map.getLayer(SITE_HIGHLIGHT_ID)) map.setFilter(SITE_HIGHLIGHT_ID, siteHighlightFilter(siteId));
}

export function setGroupVisibility(map: MapLibreMap, group: LayerGroup, on: boolean) {
  for (const id of group.layerIds) {
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
  }
}
