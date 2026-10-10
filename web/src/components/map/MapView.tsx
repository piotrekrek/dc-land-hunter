import maplibregl, { type StyleSpecification } from "maplibre-gl";
import { useEffect, useRef, useState } from "react";

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
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");

    // Fall back when the basemap style fails to load, or hangs (blocked networks often drop silently).
    let settled = false;
    const useFallback = () => {
      if (settled) return;
      settled = true;
      setBasemapFailed(true);
      map.setStyle(FALLBACK_STYLE);
    };
    const timer = window.setTimeout(useFallback, BASEMAP_TIMEOUT_MS);
    map.once("style.load", () => {
      settled = true;
      window.clearTimeout(timer);
    });
    map.on("error", useFallback);
    return () => {
      window.clearTimeout(timer);
      map.remove();
    };
  }, []);

  return (
    <div className="relative h-full w-full">
      <div ref={container} className="h-full w-full" data-testid="map" aria-label="Mapa" role="region" />
      {basemapFailed && (
        <div className="absolute top-3 left-3 rounded-md border bg-surface px-3 py-1.5 text-xs text-muted-foreground shadow-sm">
          Podkład mapowy niedostępny – mapa działa bez tła.
        </div>
      )}
    </div>
  );
}
