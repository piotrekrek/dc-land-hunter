import { useInfiniteQuery } from "@tanstack/react-query";
import { MapPinned, LoaderCircle } from "lucide-react";
import { useMemo } from "react";
import { Link, useSearchParams } from "react-router";

import { api, unwrap } from "@/api/client";
import { EmptyState } from "@/components/EmptyState";
import { mapsUrl } from "@/components/map/popup";
import { PageScroll } from "@/components/PageScroll";
import { ScoreBadge } from "@/components/sites/ScoreBadge";
import { SiteFilterBar } from "@/components/sites/SiteFilterBar";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatHa, formatMW, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";
import { parseFilters, toParams, type SiteFilters, type SiteSummary } from "@/lib/sites";

const PAGE = 50; // the API accepts up to 200 per request; pages are fetched with an offset
const GRID_BADGE: Record<SiteSummary["grid_badge"], BadgeProps["variant"]> = {
  "Opublikowana moc": "ok",
  "Bliskość sieci": "accent",
  "Brak danych": "neutral",
};

function SiteRow({ site, selected, onSelect }: { site: SiteSummary; selected: boolean; onSelect: (site: SiteSummary) => void }) {
  return (
    <li data-testid="site-row" className={cn("rounded-md", selected ? "bg-surface-2" : "hover:bg-surface-2")}>
      <button
        type="button"
        aria-pressed={selected}
        onClick={() => onSelect(site)}
        className="flex w-full gap-3 rounded-md p-3 text-left focus-visible:outline-2 focus-visible:outline-accent"
      >
        <ScoreBadge score={site.score} excluded={site.excluded} />
        <span className="block min-w-0 flex-1">
          <span className="block font-medium">
            {formatHa(site.area_ha)} · ok. {formatMW(site.estimated_mw)}
          </span>
          <span className="mt-1 flex flex-wrap gap-1.5">
            <Badge variant={GRID_BADGE[site.grid_badge]}>{site.grid_badge}</Badge>
            {site.excluded && <Badge variant="bad">Wykluczona</Badge>}
            {site.large_campus && <Badge variant="accent">Duży kampus</Badge>}
            {site.split_suggested && <Badge variant="warn">Do podziału</Badge>}
          </span>
          <span className="mt-1 block text-xs text-muted-foreground">
            Kompletność danych {formatPercent(site.completeness)}
            {site.upside_score > site.score && <> · po wyjaśnieniu brakujących danych do {Math.round(site.upside_score)} pkt</>}
          </span>
        </span>
      </button>
      {/* Aligned with the text column: p-3 (12) + score badge (48) + gap (12) = 72px. */}
      {/* py-3 with -my-3 makes each link a 44px tap target without changing the row's height. */}
      <div className="flex gap-4 pb-3 pl-[72px]">
        <Link
          to={`/lokalizacje/${encodeURIComponent(site.site_id)}`}
          className="-my-3 py-3 text-sm text-accent underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-accent"
        >
          Więcej informacji
        </Link>
        <a
          href={mapsUrl(site.lon, site.lat)}
          target="_blank"
          rel="noopener noreferrer"
          className="-my-3 py-3 text-sm text-accent underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-accent"
        >
          Mapy Google
        </a>
      </div>
    </li>
  );
}

/** The ranked list of candidate sites. Filter state is kept in the URL (brief 8.1). */
export function SiteList({ selectedId = null, onSelect }: { selectedId?: string | null; onSelect: (site: SiteSummary) => void }) {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => parseFilters(params), [params]);

  // Filters change the key, which starts again from the first page.
  const sites = useInfiniteQuery({
    queryKey: ["sites", filters],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/sites", {
          params: {
            query: {
              sort: filters.sort,
              min_mw: filters.minMw,
              min_ha: filters.minHa,
              include_excluded: filters.includeExcluded,
              limit: PAGE,
              offset: pageParam,
            },
          },
        }),
      ),
    getNextPageParam: (last) => (last.offset + last.items.length < last.total ? last.offset + last.items.length : undefined),
  });

  const items = sites.data?.pages.flatMap((p) => p.items) ?? [];
  const total = sites.data?.pages[0]?.total ?? 0;

  const update = (next: SiteFilters) => {
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
      {sites.data && total === 0 && (
        <EmptyState icon={MapPinned} title="Brak lokalizacji dla tych filtrów">
          Żadna lokalizacja nie pasuje do bieżących filtrów. Poszerz filtry albo pokaż wykluczone lokalizacje.
        </EmptyState>
      )}
      {sites.data && total > 0 && (
        <PageScroll label="Wyniki" className="min-h-0 flex-1">
          <p className="px-4 pt-3 text-xs text-muted-foreground">
            Wyników: {total}. Moc to szacunek na podstawie powierzchni. Wynik nie jest obietnicą przyłączenia: pokazuje, jak
            mocno lokalizacja pasuje do profilu i jakie dane jeszcze brakują.
          </p>
          <ul className="flex flex-col gap-1 p-2" aria-label="Ranking lokalizacji">
            {items.map((site) => (
              <SiteRow key={site.site_id} site={site} selected={site.site_id === selectedId} onSelect={onSelect} />
            ))}
          </ul>
          {sites.hasNextPage && (
            <div className="p-3">
              <Button
                variant="outline"
                size="sm"
                className="w-full"
                onClick={() => void sites.fetchNextPage()}
                disabled={sites.isFetchingNextPage}
              >
                Pokaż więcej ({items.length} z {total})
              </Button>
            </div>
          )}
        </PageScroll>
      )}
    </div>
  );
}
