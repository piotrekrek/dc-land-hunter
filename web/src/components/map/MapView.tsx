import maplibregl, { type StyleSpecification } from "maplibre-gl";
import { useCallback, useEffect, useRef, useState } from "react";

import { LayerControl } from "./LayerControl";
import { LAYER_GROUPS, addGridLayers, setGroupVisibility } from "./layers";

/** Free OSM-based vector basemap (no API key). Override with VITE_BASEMAP_STYLE. */
const BASEMAP_STYLE: string = import.meta.env.VITE_BASEMAP_STYLE ?? "https://tiles.openfreemap.org/styles/positron";

/** Rough extent of the AOI (Poland west of the Vistula); replaced by the real AOI bounds in Phase 1. */
const AOI_BOUNDS: [[number, number], [number, number]] = [
  [14.1, 49.35],
  [21.4, 54.85],
];

const BASEMAP_TIMEOUT_MS = 8000;

/** Used when the basemap cannot load (offline, blocked): the map still works, without a background map. */
const FALLBACK_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "background", type: "background", paint: { "background-color": "#e9edf1" } }],
};

export function MapView() {
  const container = useRef<HTMLDivElement>(null);
  const [basemapFailed, setBasemapFailed] = useState(false);
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
    map.on("error", () => {
      if (!settled) switchToFallback();
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
      {basemapFailed && (
        <div className="absolute bottom-8 left-3 rounded-md border bg-surface px-3 py-1.5 text-xs text-muted-foreground shadow-sm">
          Podkład mapowy niedostępny – mapa działa bez tła.
        </div>
      )}
    </div>
  );
}
