import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { connectionCost } from "@/lib/connectionCost";
import { formatPln } from "@/lib/format";

/** What securing a connection ties up, for the site's estimated power or any other value the user types. */
export function ConnectionCostCalculator({ estimatedMw }: { estimatedMw: number }) {
  const [mw, setMw] = useState(String(Math.round(estimatedMw)));
  const value = Number(mw.replace(",", "."));
  const cost = connectionCost(value);
  return (
    <section className="mt-6 rounded-md border bg-surface p-4" aria-labelledby="calc-title" data-testid="cost-calculator">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="calc-title" className="text-base font-semibold">
          Koszt przyłączenia
        </h2>
        <Badge variant="warn">wymaga weryfikacji prawnej</Badge>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">
        Wg zmian w Prawie energetycznym z 30.04.2026. Moc domyślnie z szacunku dla tej lokalizacji; możesz ją zmienić.
      </p>
      <label className="mt-3 flex items-center gap-2 text-sm">
        Moc przyłączeniowa
        <input
          type="text"
          inputMode="decimal"
          value={mw}
          onChange={(e) => setMw(e.target.value)}
          className="h-9 w-24 rounded-md border bg-surface px-2 text-right tabular-nums"
        />
        MW
      </label>
      <dl className="mt-3 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm tabular-nums">
        <dt>Opłata za wniosek (wydana)</dt>
        <dd className="text-right">{formatPln(cost.fee)}</dd>
        <dt>Zaliczka na przyłączenie (zamrożona)</dt>
        <dd className="text-right">{formatPln(cost.advance)}</dd>
        <dt>Zabezpieczenie (zamrożone)</dt>
        <dd className="text-right">{formatPln(cost.security)}</dd>
        <dt className="border-t pt-1 font-semibold">Zamrożone razem</dt>
        <dd className="border-t pt-1 text-right font-semibold" data-testid="frozen-total">
          {formatPln(cost.frozen)}
        </dd>
      </dl>
      <p className="mt-3 text-xs text-muted-foreground">
        Warunki przyłączenia obowiązują 12 miesięcy. Przeniesienie warunków na kupującego jest prawnie sporne (wyrok Sądu
        Apelacyjnego z 2013 r. uznał je za prawa publiczne, których nie można przenieść). W praktyce teren trzyma się w
        spółce celowej (SPV) i sprzedaje jej udziały. To tylko uwaga, nie porada prawna.
      </p>
    </section>
  );
}
