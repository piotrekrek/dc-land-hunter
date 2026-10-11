import { HA_OPTIONS, MW_OPTIONS, SORT_OPTIONS, type SiteFilters } from "@/lib/sites";

interface Props {
  filters: SiteFilters;
  onChange: (next: SiteFilters) => void;
}

const selectClass = "h-9 rounded-md border bg-surface px-2 text-sm";

/** Filters for the ranked list. Labels are Polish; the values are written to the URL by the parent. */
export function SiteFilterBar({ filters, onChange }: Props) {
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
        Moc od
        <select className={selectClass} value={filters.minMw} onChange={(e) => onChange({ ...filters, minMw: Number(e.target.value) })}>
          {MW_OPTIONS.map((mw) => (
            <option key={mw} value={mw}>
              {mw === 0 ? "dowolna" : `${mw} MW`}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Powierzchnia od
        <select className={selectClass} value={filters.minHa} onChange={(e) => onChange({ ...filters, minHa: Number(e.target.value) })}>
          {HA_OPTIONS.map((ha) => (
            <option key={ha} value={ha}>
              {ha === 0 ? "dowolna" : `${ha} ha`}
            </option>
          ))}
        </select>
      </label>
      <label className="flex h-9 items-center gap-2 text-sm">
        <input type="checkbox" checked={filters.includeExcluded} onChange={(e) => onChange({ ...filters, includeExcluded: e.target.checked })} />
        Pokaż wykluczone
      </label>
    </form>
  );
}
