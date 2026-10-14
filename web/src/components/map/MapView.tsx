import maplibregl, { type StyleSpecification } from "maplibre-gl";
import { useCallback, useEffect, useRef, useState } from "react";

import { proxyBasemapRequest } from "./basemap";
import { LayerControl } from "./LayerControl";
import { LAYER_GROUPS, LINE_LAYER_IDS, OWN_SOURCES, addGridLayers, setGroupVisibility } from "./layers";
import { linePopup, renderPopup, stationPopup } from "./popup";

/** Free OSM-based vector basemap (no API key). Override with VITE_BASEMAP_STYLE. */
const BASEMAP_STYLE: string = import.meta.env.VITE_BASEMAP_STYLE || "https://tiles.openfreemap.org/styles/dark";

/** Rough extent of the AOI (Poland west of the Vistula); replaced by the real AOI bounds in Phase 1. */
const AOI_BOUNDS: [[number, number], [number, number]] = [
  [14.1, 49.35],
  [21.4, 54.85],
];

const BASEMAP_TIMEOUT_MS = 8000;

/** Clickable layers, in priority order: a station wins over a line passing through it. */
const CLICKABLE: string[] = ["substations-circle", ...LINE_LAYER_IDS];
/** Lines are thin, so a click within this many pixels counts as a hit. */
const HIT_PX = 6;

function featureAt(map: maplibregl.Map, point: { x: number; y: number }) {
  const layers = CLICKABLE.filter((id) => map.getLayer(id));
  if (!layers.length) return undefined;
  const box: [[number, number], [number, number]] = [
    [point.x - HIT_PX, point.y - HIT_PX],
    [point.x + HIT_PX, point.y + HIT_PX],
  ];
  const hits = map.queryRenderedFeatures(box, { layers: [...layers] });
  return CLICKABLE.map((id) => hits.find((f) => f.layer.id === id)).find(Boolean);
}

/** Used when the basemap cannot load (offline, blocked): the map still works, without a background map. */
const FALLBACK_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [
    {
      id: "background",
      type: "background",
      paint: { "background-color": "#e9edf1" },
    },
  ],
};

export function MapView() {
  const container = useRef<HTMLDivElement>(null);
  const [basemapFailed, setBasemapFailed] = useState(false);
  const [basemapIssue, setBasemapIssue] = useState<string | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [visible, setVisible] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(LAYER_GROUPS.map((g) => [g.id, g.defaultOn])),
  );
  const visibleRef = useRef(visible);
  visibleRef.current = visible;

  const toggle = useCallback((groupId: string, on: boolean) => {
    setVisible((v) => ({ ...v, [groupId]: on }));
    const group = LAYER_GROUPS.find((g) => g.id === groupId);
    if (mapRef.current && group) setGroupVisibility(mapRef.current, group, on);
  }, []);

  useEffect(() => {
    if (!container.current) return;
    const map = new maplibregl.Map({
      container: container.current,
      style: BASEMAP_STYLE,
      transformRequest: (url) => proxyBasemapRequest(url),
      bounds: AOI_BOUNDS,
      fitBoundsOptions: { padding: 24 },
      hash: "mapa", // map position lives in the URL (#mapa=zoom/lat/lng) so a view can be shared
      attributionControl: { compact: true },
      maxBounds: [
        [10.5, 47.5],
        [27.5, 56.5],
      ],
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");

    let popup: maplibregl.Popup | null = null;
    map.on("click", (e: maplibregl.MapMouseEvent) => {
      popup?.remove();
      const feature = featureAt(map, e.point);
      if (!feature) return;
      const props = feature.properties ?? {};
      const isStation = feature.layer.id === "substations-circle";
      // A station is a point: use its own position, not where the cursor landed.
      const [lng, lat] =
        isStation && feature.geometry.type === "Point"
          ? (feature.geometry.coordinates as [number, number])
          : [e.lngLat.lng, e.lngLat.lat];
      const model = isStation ? stationPopup(props, lng, lat) : linePopup(props, lng, lat);
      popup = new maplibregl.Popup({
        maxWidth: "320px",
        className: "dclh-popup-shell",
      })
        .setLngLat([lng, lat])
        .setDOMContent(renderPopup(model, !isStation))
        .addTo(map);
    });
    map.on("mousemove", (e: maplibregl.MapMouseEvent) => {
      map.getCanvas().style.cursor = featureAt(map, e.point) ? "pointer" : "";
    });

    // Fall back when the basemap style fails to load, or hangs (blocked networks often drop silently).
    let settled = false;
    const switchToFallback = () => {
      if (settled) return;
      settled = true;
      setBasemapFailed(true);
      map.setStyle(FALLBACK_STYLE);
    };
    const timer = window.setTimeout(switchToFallback, BASEMAP_TIMEOUT_MS);
    map.on("style.load", () => {
      // Runs for the basemap and again for the fallback style: setStyle() drops our layers.
      addGridLayers(map, visibleRef.current);
    });
    map.once("style.load", () => {
      settled = true;
      window.clearTimeout(timer);
    });
    // Only a basemap style failure justifies the fallback. Errors after the style loaded (e.g. 404 tiles for
    // our own, still empty, Martin layers) must not replace the basemap.
    map.on("error", (e: { error?: Error; sourceId?: string }) => {
      if (!settled) {
        switchToFallback();
        return;
      }
      // After the style loaded, errors of our own (still empty) Martin sources are expected; anything else is the
      // basemap's tiles, glyphs or sprites failing (blocked host, ad blocker): surface it instead of a silent blank map.
      if (!e.sourceId || !OWN_SOURCES.has(e.sourceId)) {
        setBasemapIssue((prev) => prev ?? (e.error?.message || "nieznany błąd"));
      }
    });
    // The container is `hidden` (0x0) on phones until "Mapa" is selected: resize when it gets a real size.
    const resizer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => map.resize());
    resizer?.observe(container.current);
    return () => {
      resizer?.disconnect();
      window.clearTimeout(timer);
      mapRef.current = null;
      map.remove();
    };
  }, []);

  return (
    <div className="relative h-full w-full">
      <div ref={container} className="h-full w-full" data-testid="map" aria-label="Mapa" role="region" />
      <LayerControl visible={visible} onToggle={toggle} />
      {!basemapFailed && basemapIssue && (
        <div className="absolute bottom-8 left-3 max-w-sm rounded-md border bg-surface px-3 py-1.5 text-xs text-muted-foreground shadow-sm">
          Podkład mapowy ładuje się z błędami: {basemapIssue}
        </div>
      )}
      {basemapFailed && (
        <div className="absolute bottom-8 left-3 rounded-md border bg-surface px-3 py-1.5 text-xs text-muted-foreground shadow-sm">
          Podkład mapowy niedostępny – mapa działa bez tła.
        </div>
      )}
    </div>
  );
}
