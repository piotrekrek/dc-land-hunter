import { formatNumber } from "@/lib/format";
import type { SiteDetail } from "@/lib/sites";
import { toCheck } from "@/lib/toCheck";

/** "Do sprawdzenia": every criterion without data, with who to ask. Biggest possible gain first. */
export function ToCheck({ items }: { items: SiteDetail["items"] }) {
  const list = toCheck(items);
  if (list.length === 0) return null;
  return (
    <section className="mt-6 rounded-md border bg-surface p-4" aria-labelledby="to-check-title" data-testid="to-check">
      <h2 id="to-check-title" className="text-base font-semibold">
        Do sprawdzenia ({list.length})
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Tych danych nie mamy. Brak danych nie oznacza, że lokalizacja jest zła. Kolejność: od największego możliwego zysku w punktach.
      </p>
      <ol className="mt-3 list-none p-0">
        {list.map((e) => (
          <li key={e.key} className="border-t py-2 text-sm first:border-t-0">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="font-medium">{e.label}</span>
              {e.gain > 0 && <span className="text-xs text-muted-foreground tabular-nums">do +{formatNumber(e.gain, 1)} pkt</span>}
            </div>
            <p className="mt-0.5 text-muted-foreground">{e.who}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}
