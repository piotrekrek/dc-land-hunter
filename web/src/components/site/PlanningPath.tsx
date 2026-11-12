import { planningRows } from "@/lib/planningPath";
import type { SiteDetail } from "@/lib/sites";

/** OUZ, the legal route to a permit and the zone limits from the plan ogólny. Hidden when the register has no plan. */
export function PlanningPath({ features }: { features: SiteDetail["features"] }) {
  const rows = planningRows(features);
  if (rows.length === 0) return null;
  return (
    <section className="mt-6 rounded-md border bg-surface p-4" aria-labelledby="planning-title" data-testid="planning-path">
      <h2 id="planning-title" className="text-base font-semibold">
        Ścieżka planistyczna
      </h2>
      <dl className="mt-3 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm">
        {rows.map((r) => (
          <div key={r.label} className="contents">
            <dt>{r.label}</dt>
            <dd className="text-right font-medium">{r.value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-xs text-muted-foreground">
        Źródło: Rejestr Urbanistyczny. Poza OUZ w gminie z planem ogólnym nie wydaje się decyzji o warunkach zabudowy.
      </p>
    </section>
  );
}
