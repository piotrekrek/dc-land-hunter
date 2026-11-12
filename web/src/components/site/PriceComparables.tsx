import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, shouldRetry, unwrap } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDate, formatNumber } from "@/lib/format";
import {
  formatDateRange,
  formatRange,
  formatTransactions,
  formatZlM2,
  OUTLIER_WARNING,
  parseAskingPrice,
  WIDENED_NOTE,
  type PriceGroup,
  type SitePrices,
} from "@/lib/prices";
import { CONFIDENCE_LABEL } from "@/lib/sites";

function Group({ label, group }: { label: string; group: PriceGroup }) {
  const range = formatRange(group);
  const dates = formatDateRange(group);
  return (
    <div className="rounded-md border p-3" data-testid={`price-group-${label}`}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      {group.count === 0 ? (
        <dd className="mt-1 text-sm text-muted-foreground">brak transakcji</dd>
      ) : (
        <dd className="mt-1">
          <span className="font-semibold tabular-nums" data-testid="price-median">
            {formatZlM2(group.median_zl_m2)}
          </span>
          <span className="ml-1 text-xs text-muted-foreground">mediana</span>
          {range && <div className="text-sm tabular-nums">typowo {range}</div>}
          <div className="text-xs text-muted-foreground">
            {formatTransactions(group.count)}
            {dates ? `, ${dates}` : ""}
          </div>
        </dd>
      )}
    </div>
  );
}

function AskingPrice({ siteId, prices }: { siteId: string; prices: SitePrices }) {
  const client = useQueryClient();
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? (prices.asking_zl_m2 != null ? formatNumber(prices.asking_zl_m2, 2) : "");
  const parsed = parseAskingPrice(shown);
  const invalid = draft !== null && draft.trim() !== "" && parsed === null;
  const save = useMutation({
    mutationFn: (zl_m2: number | null) =>
      unwrap(api.PUT("/api/sites/{site_id}/asking-price", { params: { path: { site_id: siteId } }, body: { zl_m2 } })),
    onSuccess: (data) => {
      client.setQueryData(["site-prices", siteId], data);
      setDraft(null);
    },
  });
  const dirty = draft !== null;

  return (
    <div className="mt-4">
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (draft !== null && draft.trim() === "") save.mutate(null);
          else if (parsed !== null) save.mutate(parsed);
        }}
      >
        <label className="text-sm">
          Cena ofertowa (zł/m²)
          <input
            type="text"
            inputMode="decimal"
            value={shown}
            onChange={(e) => setDraft(e.target.value)}
            aria-invalid={invalid}
            className="mt-1 block h-9 w-32 rounded-md border bg-surface px-2 text-right tabular-nums"
          />
        </label>
        <Button type="submit" size="sm" disabled={!dirty || invalid || save.isPending}>
          Zapisz
        </Button>
        {prices.asking_zl_m2 != null && !dirty && (
          <Button type="button" variant="outline" size="sm" disabled={save.isPending} onClick={() => save.mutate(null)}>
            Usuń
          </Button>
        )}
      </form>
      {invalid && (
        <p role="alert" className="mt-2 text-sm text-bad">
          Wpisz liczbę większą od zera, np. 25 albo 12,5.
        </p>
      )}
      {save.isError && (
        <p role="alert" className="mt-2 text-sm text-bad">
          Nie udało się zapisać ceny. Spróbuj ponownie.
        </p>
      )}
      {prices.asking_outlier && (
        <p role="status" className="mt-2 text-sm font-medium text-warn" data-testid="asking-outlier">
          {OUTLIER_WARNING}
        </p>
      )}
      <p className="mt-1 text-xs text-muted-foreground">Jedna wartość dla całego zespołu.</p>
    </div>
  );
}

/** RCN land-price comparables around the site and the asking price. Informational: no score uses it. */
export function PriceComparables({ siteId }: { siteId: string }) {
  const query = useQuery({
    queryKey: ["site-prices", siteId],
    queryFn: () => unwrap(api.GET("/api/sites/{site_id}/prices", { params: { path: { site_id: siteId } } })),
    retry: (count, error) => shouldRetry(count, error, 2),
  });
  const prices = query.data;
  const comp = prices?.comparables;

  return (
    <section className="mt-6 rounded-md border bg-surface p-4" aria-labelledby="prices-title" data-testid="price-comparables">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="prices-title" className="text-base font-semibold">
          Ceny transakcyjne (RCN)
        </h2>
        {comp && <Badge variant={comp.confidence === "verified" ? "ok" : "warn"}>{CONFIDENCE_LABEL[comp.confidence]}</Badge>}
      </div>
      {query.isPending && (
        <p className="mt-2 text-sm text-muted-foreground" role="status">
          Wczytuję ceny…
        </p>
      )}
      {query.isError && (
        <p role="alert" className="mt-2 text-sm text-bad">
          Nie udało się wczytać cen transakcyjnych.
        </p>
      )}
      {prices && comp && (
        <>
          {comp.confidence === "missing" ? (
            <p className="mt-2 text-sm text-muted-foreground">
              Brak zaimportowanych transakcji z rejestru cen (RCN) dla tej okolicy w ostatnich {comp.months} miesiącach.
            </p>
          ) : (
            <>
              <p className="mt-1 text-sm text-muted-foreground">
                Niezabudowane grunty, wolny rynek, całość nieruchomości, ostatnie {comp.months} miesiące
                {comp.widened ? "" : `, w promieniu ${formatNumber(comp.radius_km, 0)} km`}.
              </p>
              {comp.widened && (
                <p className="mt-2 text-sm font-medium text-warn" data-testid="prices-widened">
                  {WIDENED_NOTE}
                </p>
              )}
              <dl className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Group label="Grunty rolne" group={comp.agricultural} />
                <Group label="Pozostałe grunty" group={comp.other} />
              </dl>
            </>
          )}
          <p className="mt-3 text-xs text-muted-foreground" data-testid="prices-source">
            Źródło: RCN (GUGiK){comp.as_of ? `, import ${formatDate(comp.as_of)}` : ""}. Informacyjnie, nie wpływa na punktację.
          </p>
          <AskingPrice key={`${siteId}-${prices.asking_zl_m2 ?? "none"}`} siteId={siteId} prices={prices} />
        </>
      )}
    </section>
  );
}
