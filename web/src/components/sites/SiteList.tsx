import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { MapPinned, LoaderCircle } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
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
import {
  DEFAULT_FILTERS,
  describeFilters,
  isDefaultFilters,
  parseFilters,
  placeLine,
  toParams,
  uniqueBySiteId,
  type SiteFilters,
  type SiteSummary,
} from "@/lib/sites";

const PAGE = 50; // the API accepts up to 200 per request; each page starts at the cursor the last one returned
const GRID_BADGE: Record<SiteSummary["grid_badge"], BadgeProps["variant"]> = {
  "Opublikowana moc": "ok",
  "Bliskość sieci": "accent",
  "Brak danych": "neutral",
};

interface RowProps {
  site: SiteSummary;
  selected: boolean;
  /** The pointer is on this site's polygon on the map. */
  hovered: boolean;
  onSelect: (site: SiteSummary) => void;
  onHover: (site: SiteSummary | null) => void;
}

function SiteRow({ site, selected, hovered, onSelect, onHover }: RowProps) {
  const rowRef = useRef<HTMLLIElement>(null);
  // A site picked or hovered on the map brings its row into view; "nearest" leaves a row that is already visible where it is.
  useEffect(() => {
    if (selected) rowRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selected]);
  useEffect(() => {
    if (hovered) rowRef.current?.scrollIntoView({ block: "nearest" });
  }, [hovered]);
  return (
    <li
      ref={rowRef}
      data-testid="site-row"
      data-hovered={hovered || undefined}
      className={cn("rounded-md", selected || hovered ? "bg-surface-2" : "hover:bg-surface-2")}
      onMouseEnter={() => onHover(site)}
      onMouseLeave={() => onHover(null)}
      onFocus={() => onHover(site)}
      onBlur={() => onHover(null)}
    >
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
          {placeLine(site) && <span className="block text-xs text-muted-foreground">{placeLine(site)}</span>}
          <span className="mt-1 flex flex-wrap gap-1.5">
            <Badge variant={GRID_BADGE[site.grid_badge]}>{site.grid_badge}</Badge>
            {site.excluded && <Badge variant="bad">Wykluczona</Badge>}
            {site.large_campus && <Badge variant="accent">Duży kampus</Badge>}
            {site.split_suggested && <Badge variant="warn">Do podziału</Badge>}
          </span>
          {site.weakest_label_pl && <span className="mt-1 block text-xs text-muted-foreground">najsłabsze: {site.weakest_label_pl}</span>}
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

/** "Szukaj w tym obszarze": the list follows the map. `bbox` is the map's visible box, null until the map has reported it. */
export interface ViewSearch {
  on: boolean;
  bbox: string | null;
  onChange: (on: boolean) => void;
}

/** The ranked list of candidate sites. Filter state is kept in the URL (brief 8.1). */
export function SiteList({
  selectedId = null,
  hoveredId = null,
  viewSearch,
  onSelect,
  onHover = () => {},
}: {
  selectedId?: string | null;
  /** The site under the pointer on the map. */
  hoveredId?: string | null;
  viewSearch?: ViewSearch;
  onSelect: (site: SiteSummary) => void;
  /** The pointer entered a row (the site) or left it (null). */
  onHover?: (site: SiteSummary | null) => void;
}) {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => parseFilters(params), [params]);
  const searchInView = viewSearch?.on === true;
  // While the map has not reported its box yet, the list waits instead of showing every site as "in view".
  const bbox = searchInView ? viewSearch.bbox : null;
  const waitingForMap = searchInView && bbox === null;

  // Filters and the box change the key, which starts again from the first page. Pages follow a keyset cursor,
  // so a scoring run between two requests cannot shift rows into or out of the next page.
  const sites = useInfiniteQuery({
    queryKey: ["sites", filters, bbox],
    enabled: !waitingForMap,
    // Panning keeps the previous rows on screen until the new box answers, so the list does not blink.
    placeholderData: bbox ? keepPreviousData : undefined,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/sites", {
          params: {
            query: {
              sort: filters.sort,
              min_mw: filters.minMw,
              min_ha: filters.minHa,
              include_excluded: filters.includeExcluded,
              voivodeship: filters.voivodeship || undefined,
              powiat: filters.powiat || undefined,
              bbox: bbox ?? undefined,
              grid_badge: filters.gridBadge || undefined,
              large_campus: filters.largeCampus || undefined,
              split_suggested: filters.splitSuggested || undefined,
              limit: PAGE,
              cursor: pageParam,
            },
          },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });

  // "w widoku: N z <all>": the same filters without the box, one row asked for, only while the box is on.
  const everywhere = useQuery({
    queryKey: ["sites-total", filters],
    enabled: searchInView,
    queryFn: () =>
      unwrap(
        api.GET("/api/sites", {
          params: {
            query: {
              min_mw: filters.minMw,
              min_ha: filters.minHa,
              include_excluded: filters.includeExcluded,
              voivodeship: filters.voivodeship || undefined,
              powiat: filters.powiat || undefined,
              grid_badge: filters.gridBadge || undefined,
              large_campus: filters.largeCampus || undefined,
              split_suggested: filters.splitSuggested || undefined,
              limit: 1,
            },
          },
        }),
      ),
    select: (page) => page.total,
  });

  const items = uniqueBySiteId(sites.data?.pages.flatMap((p) => p.items) ?? []);
  const total = sites.data?.pages[0]?.total ?? 0;

  const update = (next: SiteFilters) => {
    setParams(toParams(next), { replace: true });
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b p-3">
        <SiteFilterBar filters={filters} onChange={update} />
        {viewSearch && (
          <label className="mt-2 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={viewSearch.on} onChange={(e) => viewSearch.onChange(e.target.checked)} />
            Szukaj w tym obszarze
          </label>
        )}
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <p>{describeFilters(filters)}</p>
          <Button variant="ghost" size="sm" disabled={isDefaultFilters(filters)} onClick={() => update(DEFAULT_FILTERS)}>
            Wyczyść filtry
          </Button>
        </div>
      </div>
      {(sites.isPending || waitingForMap) && (
        <p className="flex items-center gap-2 p-4 text-sm text-muted-foreground" role="status">
          <LoaderCircle className="size-4 animate-spin" aria-hidden /> Wczytuję lokalizacje…
        </p>
      )}
      {sites.isError && (
        <p role="alert" className="p-4 text-sm text-bad">
          Nie udało się wczytać lokalizacji.
        </p>
      )}
      {sites.data && total === 0 && bbox && (
        <EmptyState icon={MapPinned} title="Brak lokalizacji w tym obszarze">
          W widocznej części mapy nie ma lokalizacji pasujących do filtrów. Przesuń lub oddal mapę albo wyłącz „Szukaj w tym obszarze”.
        </EmptyState>
      )}
      {sites.data && total === 0 && !bbox && (
        <EmptyState icon={MapPinned} title="Brak lokalizacji dla tych filtrów">
          Żadna lokalizacja nie pasuje do bieżących filtrów. Poszerz filtry albo pokaż wykluczone lokalizacje.
        </EmptyState>
      )}
      {sites.data && total > 0 && (
        <PageScroll label="Wyniki" className="min-h-0 flex-1">
          <p className="px-4 pt-3 text-xs text-muted-foreground">
            {bbox ? <>W widoku: {total} z {everywhere.data ?? "…"}.</> : <>Wyników: {total}.</>} Moc to szacunek na podstawie powierzchni. Wynik nie jest obietnicą przyłączenia: pokazuje, jak
            mocno lokalizacja pasuje do profilu i jakie dane jeszcze brakują.
          </p>
          <ul className="flex flex-col gap-1 p-2" aria-label="Ranking lokalizacji">
            {items.map((site) => (
              <SiteRow
                key={site.site_id}
                site={site}
                selected={site.site_id === selectedId}
                hovered={site.site_id === hoveredId}
                onSelect={onSelect}
                onHover={onHover}
              />
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
