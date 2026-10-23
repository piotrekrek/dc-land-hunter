import maplibregl, { type StyleSpecification } from "maplibre-gl";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, unwrap } from "@/api/client";
import { proxyBasemapRequest } from "./basemap";
import { CoverageControls } from "./CoverageControls";
import { LayerControl } from "./LayerControl";
import {
  LAYER_GROUPS,
  LINE_LAYER_IDS,
  OWN_SOURCES,
  SITE_CLUSTER_ID,
  SITE_FILL_ID,
  STATION_LAYER_IDS,
  addGridLayers,
  setCoverageData,
  setGroupVisibility,
  setSiteHighlight,
  siteFocus,
  clusterZoomTarget,
} from "./layers";
import { coverageCollection, type CoverageItem, type CoverageLevel, type CoverageCollection } from "@/lib/coverage";
import { type MapFocus } from "@/lib/sites";
import { useActiveTheme, type Theme } from "@/lib/theme";
import { linePopup, renderPopup, stationPopup } from "./popup";

/** Free OSM-based vector basemaps (no API key). Overrides: VITE_BASEMAP_STYLE (dark), VITE_BASEMAP_STYLE_LIGHT (light). */
const BASEMAP_STYLES: Record<Theme, string> = {
  dark: import.meta.env.VITE_BASEMAP_STYLE || "https://tiles.openfreemap.org/styles/dark",
  light: import.meta.env.VITE_BASEMAP_STYLE_LIGHT || "https://tiles.openfreemap.org/styles/liberty",
};

/** Rough extent of the AOI (Poland west of the Vistula); replaced by the real AOI bounds in Phase 1. */
const AOI_BOUNDS: [[number, number], [number, number]] = [
  [14.1, 49.35],
  [21.4, 54.85],
];

const BASEMAP_TIMEOUT_MS = 8000;
/** A candidate site is 10 to 80 ha (about 0.3 to 1 km across); this zoom shows it with its surroundings. */
const FOCUS_ZOOM = 15;

/** Clickable layers, in priority order: a station wins over a line passing through it. */
const CLICKABLE: string[] = [...STATION_LAYER_IDS, ...LINE_LAYER_IDS, SITE_FILL_ID];
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

/** The counted bubble under a point (zoomed-out view), if any. */
function bubbleAt(map: maplibregl.Map, point: { x: number; y: number }) {
  if (!map.getLayer(SITE_CLUSTER_ID)) return undefined;
  return map.queryRenderedFeatures([point.x, point.y], { layers: [SITE_CLUSTER_ID] }).find((f) => f.layer.id === SITE_CLUSTER_ID);
}

/** Used when the basemap cannot load (offline, blocked): the map still works, without a background map. */
function fallbackStyle(theme: Theme): StyleSpecification {
  return {
    version: 8,
    sources: {},
    layers: [
      {
        id: "background",
        type: "background",
        paint: { "background-color": theme === "dark" ? "#1b1f26" : "#e9edf1" },
      },
    ],
  };
}

export function MapView({
  focus = null,
  onSiteClick,
}: {
  focus?: MapFocus | null;
  /** A click on a site polygon picks that site, as picking its row in the list does. */
  onSiteClick?: (focus: MapFocus) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const onSiteClickRef = useRef(onSiteClick);
  onSiteClickRef.current = onSiteClick;
  const selectedSiteRef = useRef<string | null>(null);
  const [basemapFailed, setBasemapFailed] = useState(false);
  const [basemapIssue, setBasemapIssue] = useState<string | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const theme = useActiveTheme();
  const themeRef = useRef(theme);
  themeRef.current = theme;
  // Set by the map effect; called when the theme changes (the map effect owns the style state).
  const applyThemeRef = useRef<((theme: Theme) => void) | null>(null);
  const [visible, setVisible] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(LAYER_GROUPS.map((g) => [g.id, g.defaultOn])),
  );
  const visibleRef = useRef(visible);
  visibleRef.current = visible;
  const coverageOn = visible.coverage === true;
  const [coverageLevel, setCoverageLevel] = useState<CoverageLevel>("gmina");
  const [coverageItem, setCoverageItem] = useState<CoverageItem>("all");
  const [coverageError, setCoverageError] = useState(false);
  // Kept outside React state: the style reload (theme switch, fallback) must re-add the shapes without a refetch.
  const coverageRef = useRef<CoverageCollection>(coverageCollection([]));

  const toggle = useCallback((groupId: string, on: boolean) => {
    setVisible((v) => ({ ...v, [groupId]: on }));
    const group = LAYER_GROUPS.find((g) => g.id === groupId);
    if (mapRef.current && group) setGroupVisibility(mapRef.current, group, on);
  }, []);

  useEffect(() => {
    if (!container.current) return;
    // `shown` is the theme of the style the map has or is loading; the fallback keeps the theme it was shown in.
    let shown: Theme = themeRef.current;
    const map = new maplibregl.Map({
      container: container.current,
      style: BASEMAP_STYLES[shown],
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
      const bubble = bubbleAt(map, e.point);
      if (bubble?.geometry.type === "Point") {
        // A bubble stands for many sites: zoom in on it instead of picking one.
        const [lng, lat] = bubble.geometry.coordinates as [number, number];
        map.easeTo({ center: [lng, lat], zoom: clusterZoomTarget(map.getZoom()) });
        return;
      }
      const feature = featureAt(map, e.point);
      if (!feature) return;
      const props = feature.properties ?? {};
      if (feature.layer.id === SITE_FILL_ID) {
        // A site is picked, not described: its row in the list shows the details.
        const picked = siteFocus(props, e.lngLat);
        if (picked) onSiteClickRef.current?.(picked);
        return;
      }
      const isStation = STATION_LAYER_IDS.includes(feature.layer.id);
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
      map.getCanvas().style.cursor = bubbleAt(map, e.point) || featureAt(map, e.point) ? "pointer" : "";
    });

    // Fall back when the basemap style fails to load, or hangs (blocked networks often drop silently).
    let settled = false;
    let usingFallback = false;
    let timer = 0;
    const armTimer = () => {
      settled = false;
      window.clearTimeout(timer);
      timer = window.setTimeout(switchToFallback, BASEMAP_TIMEOUT_MS);
    };
    function switchToFallback() {
      if (settled) return;
      settled = true;
      usingFallback = true;
      setBasemapFailed(true);
      map.setStyle(fallbackStyle(shown));
    }
    armTimer();
    map.on("style.load", () => {
      // Runs for every style: setStyle() drops our layers, so they are added again after each one (basemap, theme
      // switch, fallback). Layers and visibility come from React state via visibleRef, so they survive a theme switch.
      addGridLayers(map, visibleRef.current, shown === "dark", selectedSiteRef.current, coverageRef.current);
      settled = true;
      window.clearTimeout(timer);
    });
    applyThemeRef.current = (next: Theme) => {
      if (next === shown) return;
      shown = next;
      if (usingFallback) {
        map.setStyle(fallbackStyle(next));
        return;
      }
      armTimer();
      map.setStyle(BASEMAP_STYLES[next]);
    };
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
      applyThemeRef.current = null;
      mapRef.current = null;
      map.remove();
    };
  }, []);

  // Coverage shapes for the chosen level and item, fetched only while the layer is on.
  useEffect(() => {
    if (!coverageOn) return;
    let current = true;
    setCoverageError(false);
    unwrap(
      api.GET("/api/coverage", {
        params: { query: { level: coverageLevel, item: coverageItem, include_geometry: true } },
      }),
    )
      .then((data) => {
        if (!current) return;
        coverageRef.current = coverageCollection(data.areas);
        if (mapRef.current) setCoverageData(mapRef.current, coverageRef.current);
      })
      .catch(() => {
        if (current) setCoverageError(true);
      });
    return () => {
      current = false;
    };
  }, [coverageOn, coverageLevel, coverageItem]);

  // Switch the basemap when the theme changes. The map effect above runs first, so on mount this is a no-op.
  useEffect(() => {
    applyThemeRef.current?.(theme);
  }, [theme]);

  // Fly to the site picked in the list and mark it. Declared after the effect that creates the map, so it exists.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !focus) return;
    map.resize(); // the map is hidden (0x0) on phones until "Mapa" is selected
    selectedSiteRef.current = focus.siteId;
    setSiteHighlight(map, focus.siteId);
    map.flyTo({ center: [focus.lon, focus.lat], zoom: FOCUS_ZOOM });
    const marker = new maplibregl.Marker({ color: "#2563eb" }).setLngLat([focus.lon, focus.lat]).addTo(map);
    return () => {
      marker.remove();
    };
  }, [focus]);

  return (
    <div className="relative h-full w-full">
      <div ref={container} className="h-full w-full" data-testid="map" aria-label="Mapa" role="region" />
      <LayerControl visible={visible} onToggle={toggle}>
        {coverageOn && (
          <CoverageControls level={coverageLevel} item={coverageItem} onLevel={setCoverageLevel} onItem={setCoverageItem} />
        )}
      </LayerControl>
      {coverageOn && coverageError && (
        <div role="status" className="absolute bottom-16 left-3 rounded-md border bg-surface px-3 py-1.5 text-xs text-bad shadow-sm">
          Nie udało się wczytać pokrycia danych.
        </div>
      )}
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
