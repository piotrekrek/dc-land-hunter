import type { LayerSpecification, Map as MapLibreMap, SourceSpecification } from "maplibre-gl";

/** Grid layers served by Martin from the `tiles` schema (brief 8.1: grid by voltage, substations, AOI). */
export interface LayerGroup {
  id: string;
  label: string;
  defaultOn: boolean;
  layerIds: string[];
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

const voltageColor: unknown = [
  "case",
  [">=", ["get", "voltage"], 380000],
  VOLTAGE_COLORS.v400,
  [">=", ["get", "voltage"], 220000],
  VOLTAGE_COLORS.v220,
  VOLTAGE_COLORS.v110,
];

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
  {
    id: "lines",
    type: "line",
    source: "lines",
    "source-layer": "lines",
    paint: {
      "line-color": voltageColor as never,
      "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.6, 12, 2.4],
    },
  },
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
  { id: "lines", label: "Linie 110–400 kV", defaultOn: true, layerIds: ["lines"] },
  { id: "substations", label: "Stacje (GPZ, węzły)", defaultOn: true, layerIds: ["substations-circle"] },
];

/** (Re)adds sources and layers; call after every `style.load` because `setStyle` removes them. */
export function addGridLayers(map: MapLibreMap, visible: Record<string, boolean>) {
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
