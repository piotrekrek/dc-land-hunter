import { List, Map as MapIcon } from "lucide-react";
import { useState } from "react";

import { MapView } from "@/components/map/MapView";
import { SiteList } from "@/components/sites/SiteList";
import { Button } from "@/components/ui/button";
import { type MapFocus } from "@/lib/sites";
import { cn } from "@/lib/utils";

/** "Mapa i lista": ranked list on the left, map on the right; a list/map toggle on phones. */
export function HomePage() {
  const [mobileView, setMobileView] = useState<"list" | "map">("list");
  const [focus, setFocus] = useState<MapFocus | null>(null);
  // The pointer is on a list row or on a site on the map; both sides outline that site.
  const [hover, setHover] = useState<MapFocus | null>(null);
  const [searchInView, setSearchInView] = useState(false);
  const [bbox, setBbox] = useState<string | null>(null);

  // Picking a site flies the map to it; on a phone the map is a separate view, so switch to it.
  const select = (site: { site_id: string; lon: number; lat: number }) => {
    setFocus({ siteId: site.site_id, lon: site.lon, lat: site.lat });
    setMobileView("map");
  };

  return (
    <div className="flex h-full flex-col lg:flex-row">
      <div className="flex gap-2 border-b bg-surface p-2 lg:hidden" role="group" aria-label="Widok">
        <Button variant={mobileView === "list" ? "default" : "outline"} size="sm" onClick={() => setMobileView("list")}>
          <List /> Lista
        </Button>
        <Button variant={mobileView === "map" ? "default" : "outline"} size="sm" onClick={() => setMobileView("map")}>
          <MapIcon /> Mapa
        </Button>
      </div>

      <section
        aria-label="Lista lokalizacji"
        className={cn(
          "flex min-h-0 flex-col border-r bg-surface lg:flex lg:w-[400px] lg:shrink-0",
          mobileView === "list" ? "flex flex-1 lg:flex-none" : "hidden",
        )}
      >
        <div className="border-b p-4">
          <h1 className="text-base font-semibold">Lokalizacje</h1>
          <p className="mt-1 text-xs text-muted-foreground">Ranking kandydatów pod centrum danych w obszarze zainteresowania.</p>
        </div>
        <SiteList
          selectedId={focus?.siteId}
          hoveredId={hover?.siteId}
          viewSearch={{ on: searchInView, bbox, onChange: setSearchInView }}
          onSelect={select}
          onHover={(site) => setHover(site && { siteId: site.site_id, lon: site.lon, lat: site.lat })}
        />
      </section>

      <section aria-label="Mapa" className={cn("min-h-0 flex-1 lg:block", mobileView === "map" ? "block" : "hidden")}>
        <MapView focus={focus} hover={hover} onSiteClick={setFocus} onSiteHover={setHover} onViewChange={setBbox} />
      </section>
    </div>
  );
}
