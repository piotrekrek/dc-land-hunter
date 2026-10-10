import { List, Map as MapIcon, MapPinned } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/EmptyState";
import { MapView } from "@/components/map/MapView";
import { PageScroll } from "@/components/PageScroll";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatHa, formatMW } from "@/lib/format";
import { cn } from "@/lib/utils";

/** "Mapa i lista": ranked list on the left, map on the right; a list/map toggle on phones. */
export function HomePage() {
  const [mobileView, setMobileView] = useState<"list" | "map">("list");

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
          <div className="mt-2 flex flex-wrap gap-1.5">
            <Badge>min. {formatMW(60)}</Badge>
            <Badge>min. {formatHa(10)}</Badge>
            <Badge>cały obszar (AOI)</Badge>
          </div>
        </div>
        <PageScroll label="Wyniki" className="h-auto min-h-0 flex-1">
          <EmptyState icon={MapPinned} title="Brak lokalizacji do wyświetlenia">
            Ranking lokalizacji pojawi się po pierwszym pełnym przebiegu pipeline’u (Faza 2). Teraz możesz sprawdzić stan
            źródeł danych w zakładce „Źródła danych”.
          </EmptyState>
        </PageScroll>
      </section>

      <section aria-label="Mapa" className={cn("min-h-0 flex-1 lg:block", mobileView === "map" ? "block" : "hidden")}>
        <MapView />
      </section>
    </div>
  );
}
