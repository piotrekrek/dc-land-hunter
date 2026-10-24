import { Copy } from "lucide-react";
import { useState } from "react";

import type { CheckSiteDetail } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDate, formatHa, formatMW } from "@/lib/format";

/** The corner of the sheet: which parcel this is, where, how big, when it was checked and by whom. */
export function TitleBlock({ site }: { site: CheckSiteDetail }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(site.parcel.parcel_id);
    } catch {
      /* the id stays selectable; copying is a convenience */
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };
  const rows: [string, string][] = [
    ["Gmina", site.parcel.commune],
    ["Obręb", site.parcel.obreb],
    ["Powierzchnia", formatHa(site.area_ha)],
    ["Szacowana moc", formatMW(site.estimated_mw)],
    ["Sprawdzono", formatDate(site.checked_at)],
    ["Zlecił(a)", site.requested_by],
  ];
  return (
    <div className="border border-ink bg-paper p-3 text-sm" data-testid="title-block">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-cond text-base font-medium break-all">{site.parcel.parcel_id}</span>
        <Button type="button" size="sm" variant="outline" onClick={copy} aria-label="Kopiuj identyfikator działki">
          <Copy aria-hidden /> {copied ? "Skopiowano" : "Kopiuj"}
        </Button>
      </div>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="font-cond text-xs text-ink-2">{label}</dt>
            <dd className="m-0 font-cond font-medium tabular-nums break-words">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-2 text-xs text-muted-foreground">Sprawdzenie jednej działki, poza wyszukiwaniem.</p>
      {site.outside_aoi && (
        <Badge variant="warn" className="mt-2">
          poza obszarem zainteresowania
        </Badge>
      )}
    </div>
  );
}
