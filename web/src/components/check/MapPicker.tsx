import maplibregl, { type StyleSpecification } from "maplibre-gl";
import { useEffect, useRef } from "react";

import { proxyBasemapRequest } from "@/components/map/basemap";
import { useActiveTheme, type Theme } from "@/lib/theme";

const STYLES: Record<Theme, string> = {
  dark: import.meta.env.VITE_BASEMAP_STYLE || "https://tiles.openfreemap.org/styles/dark",
  light: import.meta.env.VITE_BASEMAP_STYLE_LIGHT || "https://tiles.openfreemap.org/styles/liberty",
};

/** When the basemap cannot load the map still takes a click, on a plain background. */
function plain(theme: Theme): StyleSpecification {
  return { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": theme === "dark" ? "#1b1f26" : "#e9edf1" } }] };
}

/**
 * A small map for "Wskaż na mapie": a click drops a pin and hands the point to the page, which asks the server
 * which parcel lies there. Hover does nothing: a lookup per hover would hammer the cadastre service.
 */
export function MapPicker({ onPick }: { onPick: (lat: number, lon: number) => void }) {
  const container = useRef<HTMLDivElement>(null);
  const theme = useActiveTheme();
  const onPickRef = useRef(onPick);
  onPickRef.current = onPick;

  useEffect(() => {
    if (!container.current) return;
    const map = new maplibregl.Map({
      container: container.current,
      style: STYLES[theme],
      transformRequest: (url) => proxyBasemapRequest(url),
      center: [19.4, 52.0],
      zoom: 5.5,
      attributionControl: { compact: true },
      cooperativeGestures: true, // a page scroll on a phone must not be swallowed by the map
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
    let failed = false;
    map.on("error", () => {
      if (failed) return;
      failed = true;
      map.setStyle(plain(theme));
    });
    let pin: maplibregl.Marker | null = null;
    map.on("click", (e: maplibregl.MapMouseEvent) => {
      pin?.remove();
      pin = new maplibregl.Marker({ color: "#2b6a8f" }).setLngLat(e.lngLat).addTo(map);
      onPickRef.current(e.lngLat.lat, e.lngLat.lng);
    });
    return () => map.remove();
  }, [theme]);

  return (
    <div
      ref={container}
      role="application"
      aria-label="Mapa: kliknij działkę, żeby ją wskazać. Zamiast tego możesz wpisać numer działki w polu obok."
      className="h-72 w-full border border-ink lg:h-full lg:min-h-72"
    />
  );
}
