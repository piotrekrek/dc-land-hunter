/** Host of the default basemap; requests to it are routed through our own origin (Caddy `/basemap/*`). */
const OPENFREEMAP_ORIGIN = "https://tiles.openfreemap.org/";

/**
 * MapLibre `transformRequest`: sends basemap requests (style, TileJSON, tiles, glyphs, sprites) to `/basemap/*` on
 * our own origin, so browsers or networks that block the third-party host (ad blockers, firewalls) still get a map.
 */
export function proxyBasemapRequest(url: string, origin: string = globalThis.location?.origin ?? "") {
  if (!url.startsWith(OPENFREEMAP_ORIGIN)) return { url };
  return { url: `${origin}/basemap/${url.slice(OPENFREEMAP_ORIGIN.length)}` };
}
