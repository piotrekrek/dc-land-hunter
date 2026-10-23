import { useQuery } from "@tanstack/react-query";
import { LoaderCircle, ServerCrash } from "lucide-react";

import { api, unwrap } from "@/api/client";
import { EmptyState } from "@/components/EmptyState";
import { PageScroll } from "@/components/PageScroll";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatHa, formatKm, formatMW, formatNumber } from "@/lib/format";
import { SourcesSection } from "@/pages/SourcesPage";

const WEIGHT_LABELS: Record<string, string> = {
  grid: "Sieć elektroenergetyczna",
  zoning: "Przeznaczenie terenu",
  land: "Grunt (wielkość, kształt, właściciele)",
  connectivity: "Łączność (światłowód, miasta)",
  bonus: "Bonusy (ciepło, PSI, OZE)",
};

/** Read-only view of config/scoring.yaml. Editing with live re-ranking arrives in Phase 4. */
export function SettingsPage() {
  const { data, isPending, isError } = useQuery({
    queryKey: ["config", "scoring"],
    queryFn: () => unwrap(api.GET("/api/config/scoring")),
  });

  return (
    <PageScroll label="Ustawienia">
      <div className="mx-auto max-w-4xl p-4 md:p-6">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-xl font-semibold">Ustawienia</h1>
          <Badge>tylko podgląd · edycja w Fazie 4</Badge>
        </div>
        {isPending && <EmptyState icon={LoaderCircle} title="Wczytuję konfigurację…" className="[&_svg]:animate-spin" />}
        {isError && <EmptyState icon={ServerCrash} title="Nie udało się pobrać konfiguracji" tone="bad" />}
        {data && (
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Profil docelowy</CardTitle>
              </CardHeader>
              <CardContent>
                <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1.5 text-sm">
                  <dt className="text-muted-foreground">Minimalna moc</dt>
                  <dd>{formatMW(data.target.mw_min)}</dd>
                  <dt className="text-muted-foreground">Gęstość mocy</dt>
                  <dd>{formatNumber(data.target.mw_per_ha)}&nbsp;MW/ha</dd>
                  <dt className="text-muted-foreground">Minimalna powierzchnia</dt>
                  <dd>{formatHa(data.target.ha_min)}</dd>
                  <dt className="text-muted-foreground">„Duży kampus” powyżej</dt>
                  <dd>{formatMW(data.target.large_campus_mw)}</dd>
                  <dt className="text-muted-foreground">Linia 110 kV (pre-filtr)</dt>
                  <dd>≤ {formatKm(data.prefilter.line110_km)}</dd>
                  <dt className="text-muted-foreground">GPZ 110 kV (pre-filtr)</dt>
                  <dd>≤ {formatKm(data.prefilter.gpz110_km)}</dd>
                  <dt className="text-muted-foreground">Węzeł 220/400 kV (pre-filtr)</dt>
                  <dd>≤ {formatKm(data.prefilter.node_220_400_km)}</dd>
                </dl>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Wagi kryteriów</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="flex flex-col gap-3">
                  {Object.entries(data.weights).map(([key, value]) => (
                    <li key={key}>
                      <div className="flex justify-between text-sm">
                        <span>{WEIGHT_LABELS[key] ?? key}</span>
                        <span className="font-medium">{formatNumber(value, 0)}</span>
                      </div>
                      <div className="mt-1 h-1.5 rounded-full bg-surface-2" aria-hidden>
                        <div className="h-full rounded-full bg-accent" style={{ width: `${value}%` }} />
                      </div>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          </div>
        )}
      </div>
      <SourcesSection />
    </PageScroll>
  );
}
