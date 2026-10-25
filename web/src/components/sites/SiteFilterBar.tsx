import { useQuery } from "@tanstack/react-query";

import { api, unwrap } from "@/api/client";
import { GRID_BADGE_OPTIONS, MW_OPTIONS, SORT_OPTIONS, type SiteFilters } from "@/lib/sites";

interface Props {
  filters: SiteFilters;
  onChange: (next: SiteFilters) => void;
}

const selectClass = "h-9 rounded-md border bg-surface px-2 text-sm";

/** Areas that hold at least one kept site, by name; the coverage endpoint already leaves the empty ones out. */
function useAreas(level: "wojewodztwo" | "powiat") {
  const areas = useQuery({
    queryKey: ["site-areas", level],
    queryFn: () => unwrap(api.GET("/api/coverage", { params: { query: { level, item: "all" } } })),
    staleTime: 10 * 60_000,
  });
  return [...(areas.data?.areas ?? [])].sort((a, b) => a.name.localeCompare(b.name, "pl"));
}

/** Filters for the ranked list. Labels are Polish; the values are written to the URL by the parent. */
export function SiteFilterBar({ filters, onChange }: Props) {
  const voivodeships = useAreas("wojewodztwo");
  const powiats = useAreas("powiat").filter((a) => !filters.voivodeship || a.teryt.startsWith(filters.voivodeship));
  return (
    <form className="flex flex-wrap items-end gap-3" aria-label="Filtry lokalizacji" onSubmit={(e) => e.preventDefault()}>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Sortuj według
        <select className={selectClass} value={filters.sort} onChange={(e) => onChange({ ...filters, sort: e.target.value as SiteFilters["sort"] })}>
          {SORT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Szacowana moc od
        <select className={selectClass} value={filters.minMw} onChange={(e) => onChange({ ...filters, minMw: Number(e.target.value) })}>
          {MW_OPTIONS.map((mw) => (
            <option key={mw} value={mw}>
              {mw === 0 ? "dowolna" : `${mw} MW`}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Województwo
        <select
          className={selectClass}
          value={filters.voivodeship}
          // Another voivodeship leaves the chosen powiat outside it, so the powiat goes back to "any".
          onChange={(e) => onChange({ ...filters, voivodeship: e.target.value, powiat: "" })}
        >
          <option value="">dowolne</option>
          {voivodeships.map((a) => (
            <option key={a.teryt} value={a.teryt}>
              {a.name}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Powiat
        <select className={selectClass} value={filters.powiat} onChange={(e) => onChange({ ...filters, powiat: e.target.value })}>
          <option value="">dowolny</option>
          {powiats.map((a) => (
            <option key={a.teryt} value={a.teryt}>
              {a.name}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Dane o sieci
        <select
          className={selectClass}
          value={filters.gridBadge}
          onChange={(e) => onChange({ ...filters, gridBadge: e.target.value as SiteFilters["gridBadge"] })}
        >
          <option value="">dowolne</option>
          {GRID_BADGE_OPTIONS.map((badge) => (
            <option key={badge} value={badge}>
              {badge}
            </option>
          ))}
        </select>
      </label>
      <label className="flex h-9 items-center gap-2 text-sm">
        <input type="checkbox" checked={filters.largeCampus} onChange={(e) => onChange({ ...filters, largeCampus: e.target.checked })} />
        Duży kampus
      </label>
      <label className="flex h-9 items-center gap-2 text-sm">
        <input type="checkbox" checked={filters.splitSuggested} onChange={(e) => onChange({ ...filters, splitSuggested: e.target.checked })} />
        Do podziału
      </label>
      <label className="flex h-9 items-center gap-2 text-sm">
        <input type="checkbox" checked={filters.includeExcluded} onChange={(e) => onChange({ ...filters, includeExcluded: e.target.checked })} />
        Pokaż wykluczone
      </label>
    </form>
  );
}
