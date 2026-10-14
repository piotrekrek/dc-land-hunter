import type {
  FilterSpecification,
  LayerSpecification,
  Map as MapLibreMap,
  SourceSpecification,
} from "maplibre-gl";

/** Grid layers served by Martin from the `tiles` schema (brief 8.1: grid by voltage, substations, AOI). */
export interface LayerGroup {
  id: string;
  label: string;
  defaultOn: boolean;
  layerIds: string[];
  /** Legend swatch, for layers drawn in one colour. */
  color?: string;
}

const tileUrl = (name: string) => `${globalThis.location?.origin ?? ""}/tiles/${name}/{z}/{x}/{y}`;

const SOURCES: Record<string, SourceSpecification> = {
  aoi: { type: "vector", tiles: [tileUrl("aoi")], maxzoom: 12 },
  lines: { type: "vector", tiles: [tileUrl("lines")], minzoom: 4 },
  substations: { type: "vector", tiles: [tileUrl("substations")], minzoom: 5 },
};

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

const LAYERS: LayerSpecification[] = [
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
  ...LINE_LAYERS,
  {
    id: "substations-circle",
    type: "circle",
    source: "substations",
    "source-layer": "substations",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 5, 2, 12, 6],
      "circle-color": [
        "match",
        ["get", "cls"],
        "NODE_220_400",
        VOLTAGE_COLORS.v400,
        "GPZ_110_SN",
        VOLTAGE_COLORS.v110,
        "UNKNOWN_VOLTAGE",
        "#ffffff",
        "#6b7280",
      ],
      "circle-stroke-color": "#111827",
      "circle-stroke-width": 1,
    },
  },
];

export const LAYER_GROUPS: LayerGroup[] = [
  { id: "aoi", label: "Obszar poszukiwań (AOI)", defaultOn: true, layerIds: ["aoi-fill", "aoi-line"] },
  ...LINE_BANDS.map((band) => ({
    id: band.id,
    label: `Linie ${band.label}`,
    defaultOn: true,
    layerIds: [band.id],
    color: band.color,
  })),
  { id: "substations", label: "Stacje (GPZ, węzły)", defaultOn: true, layerIds: ["substations-circle"] },
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
export function addGridLayers(map: MapLibreMap, visible: Record<string, boolean>) {
  brightenBuildings(map);
  for (const [id, spec] of Object.entries(SOURCES)) {
    if (!map.getSource(id)) map.addSource(id, spec);
  }
  for (const layer of LAYERS) {
    if (!map.getLayer(layer.id)) map.addLayer(layer);
  }
  for (const group of LAYER_GROUPS) setGroupVisibility(map, group, visible[group.id] ?? group.defaultOn);
}

export function setGroupVisibility(map: MapLibreMap, group: LayerGroup, on: boolean) {
  for (const id of group.layerIds) {
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
  }
}
