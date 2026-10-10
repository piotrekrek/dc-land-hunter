import { useQuery } from "@tanstack/react-query";
import { MapPinned, LoaderCircle } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { api, unwrap } from "@/api/client";
import { EmptyState } from "@/components/EmptyState";
import { ScoreBadge } from "@/components/sites/ScoreBadge";
import { SiteFilterBar } from "@/components/sites/SiteFilterBar";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatHa, formatMW, formatPercent } from "@/lib/format";
import { parseFilters, toParams, type SiteFilters, type SiteSummary } from "@/lib/sites";

const PAGE = 50;
const GRID_BADGE: Record<SiteSummary["grid_badge"], BadgeProps["variant"]> = {
  "Opublikowana moc": "ok",
  "Bliskość sieci": "accent",
  "Brak danych": "neutral",
};

function SiteRow({ site }: { site: SiteSummary }) {
  return (
    <li>
      <Link
        to={`/lokalizacje/${encodeURIComponent(site.site_id)}`}
        className="flex gap-3 rounded-md p-3 hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-accent"
        data-testid="site-row"
      >
        <ScoreBadge score={site.score} excluded={site.excluded} />
        <div className="min-w-0 flex-1">
          <p className="font-medium">
            {formatHa(site.area_ha)} · ok. {formatMW(site.estimated_mw)}
          </p>
          <div className="mt-1 flex flex-wrap gap-1.5">
            <Badge variant={GRID_BADGE[site.grid_badge]}>{site.grid_badge}</Badge>
            {site.excluded && <Badge variant="bad">Wykluczona</Badge>}
            {site.large_campus && <Badge variant="accent">Duży kampus</Badge>}
            {site.split_suggested && <Badge variant="warn">Do podziału</Badge>}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Kompletność danych {formatPercent(site.completeness)}
            {site.upside_score > site.score && <> · po wyjaśnieniu brakujących danych do {Math.round(site.upside_score)} pkt</>}
          </p>
        </div>
      </Link>
    </li>
  );
}

/** The ranked list of candidate sites. Filter state is kept in the URL (brief 8.1). */
export function SiteList() {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => parseFilters(params), [params]);
  const [limit, setLimit] = useState(PAGE);

  const sites = useQuery({
    queryKey: ["sites", filters, limit],
    queryFn: () =>
      unwrap(
        api.GET("/api/sites", {
          params: {
            query: {
              sort: filters.sort,
              min_mw: filters.minMw,
              min_ha: filters.minHa,
              include_excluded: filters.includeExcluded,
              limit,
            },
          },
        }),
      ),
  });

  const update = (next: SiteFilters) => {
    setLimit(PAGE);
    setParams(toParams(next), { replace: true });
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b p-3">
        <SiteFilterBar filters={filters} onChange={update} />
      </div>
      {sites.isPending && (
        <p className="flex items-center gap-2 p-4 text-sm text-muted-foreground" role="status">
          <LoaderCircle className="size-4 animate-spin" aria-hidden /> Wczytuję lokalizacje…
        </p>
      )}
      {sites.isError && (
        <p role="alert" className="p-4 text-sm text-bad">
          Nie udało się wczytać lokalizacji.
        </p>
      )}
      {sites.data && sites.data.total === 0 && (
        <EmptyState icon={MapPinned} title="Brak lokalizacji dla tych filtrów">
          Poluźnij filtry albo poczekaj na pierwszy przebieg oceny. Dane działek pojawią się po podłączeniu źródeł
          (Faza 2).
        </EmptyState>
      )}
      {sites.data && sites.data.total > 0 && (
        <>
          <p className="px-4 pt-3 text-xs text-muted-foreground">
            Wyników: {sites.data.total}. Wynik nie jest obietnicą przyłączenia: pokazuje, jak mocno lokalizacja pasuje do
            profilu i jakie dane jeszcze brakują.
          </p>
          <ul className="flex flex-col gap-1 p-2" aria-label="Ranking lokalizacji">
            {sites.data.items.map((site) => (
              <SiteRow key={site.site_id} site={site} />
            ))}
          </ul>
          {sites.data.items.length < sites.data.total && (
            <div className="p-3">
              <Button variant="outline" size="sm" className="w-full" onClick={() => setLimit((l) => Math.min(l + PAGE, 200))}>
                Pokaż więcej ({sites.data.items.length} z {sites.data.total})
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
