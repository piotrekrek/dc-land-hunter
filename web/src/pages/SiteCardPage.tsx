import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, CircleAlert, MapPinned } from "lucide-react";
import { Link, useParams } from "react-router";

import { api, unwrap } from "@/api/client";
import { EmptyState } from "@/components/EmptyState";
import { PageScroll } from "@/components/PageScroll";
import { ScoreBadge } from "@/components/sites/ScoreBadge";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { formatDate, formatHa, formatMW, formatNumber, formatPercent } from "@/lib/format";
import { CATEGORY_LABEL, CONFIDENCE_LABEL, type Confidence, type SiteDetail } from "@/lib/sites";

const CONFIDENCE_VARIANT: Record<Confidence, BadgeProps["variant"]> = { verified: "ok", estimated: "accent", missing: "neutral" };
const CATEGORY_ORDER = ["grid", "zoning", "land", "connectivity", "bonus", "penalty"];

function Breakdown({ site }: { site: SiteDetail }) {
  const groups = CATEGORY_ORDER.map((cat) => ({
    cat,
    items: site.items.filter((i) => i.category === cat),
    summary: site.categories.find((c) => c.category === cat),
  })).filter((g) => g.items.length > 0);

  return (
    <div className="flex flex-col gap-4">
      {groups.map(({ cat, items, summary }) => (
        <Card key={cat} className="p-4" data-testid="breakdown-group">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="font-semibold">{CATEGORY_LABEL[cat] ?? cat}</h2>
            {summary && (
              <p className="text-sm text-muted-foreground">
                {formatNumber(summary.points, 0)} z {formatNumber(summary.max_points, 0)} pkt
              </p>
            )}
          </div>
          <ul className="mt-3 flex flex-col divide-y">
            {items.map((item) => (
              <li key={item.key} className="flex flex-col gap-1 py-2.5 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">{item.label_pl}</span>
                  <span className="tabular-nums">
                    {item.points >= 0 ? "+" : ""}
                    {formatNumber(item.points, 1)} / {formatNumber(item.max_points, 1)}
                  </span>
                </div>
                <p className="text-muted-foreground">{item.explanation_pl}</p>
                <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                  <Badge variant={CONFIDENCE_VARIANT[item.confidence]}>{CONFIDENCE_LABEL[item.confidence]}</Badge>
                  {item.source_id && (
                    <span>
                      źródło: {item.source_id}
                      {item.as_of ? `, stan na ${formatDate(item.as_of)}` : ""}
                    </span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      ))}
    </div>
  );
}

function SiteCard({ site }: { site: SiteDetail }) {
  const parcels = site.parcel_ids.length;
  return (
    <>
      <div className="flex flex-wrap items-start gap-4">
        <ScoreBadge score={site.score} excluded={site.excluded} className="size-16 text-lg" />
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold">Lokalizacja: {formatHa(site.area_ha)}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Szacowana moc {formatMW(site.estimated_mw)} (6 MW na hektar, wartość orientacyjna)
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <Badge variant={site.grid_badge === "Opublikowana moc" ? "ok" : site.grid_badge === "Bliskość sieci" ? "accent" : "neutral"}>
              {site.grid_badge}
            </Badge>
            {site.excluded && <Badge variant="bad">Wykluczona</Badge>}
            {site.large_campus && <Badge variant="accent">Duży kampus: wymaga węzła 220/400 kV</Badge>}
            {site.split_suggested && <Badge variant="warn">Powyżej 80 ha: rozważ podział</Badge>}
            {site.flags.map((f) => (
              <Badge key={f}>{f}</Badge>
            ))}
          </div>
        </div>
      </div>

      <dl className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Fact label="Działki" value={`${parcels}`} />
        <Fact label="Kompletność danych" value={formatPercent(site.completeness)} />
        <Fact label="Po wyjaśnieniu danych" value={`do ${formatNumber(site.upside_score, 0)} pkt`} />
        <Fact label="Dane z" value={formatDate(site.scored_at)} />
      </dl>

      {site.excluded && (
        <Card className="mt-5 flex gap-3 border-bad p-4" role="note">
          <CircleAlert className="mt-0.5 size-5 shrink-0 text-bad" aria-hidden />
          <div>
            <p className="font-medium">Lokalizacja nie spełnia warunków minimalnych</p>
            <ul className="mt-2 list-disc pl-5 text-sm">
              {site.exclusions.map((e) => (
                <li key={e.key}>{e.reason_pl}</li>
              ))}
            </ul>
          </div>
        </Card>
      )}

      <h2 className="mt-6 text-base font-semibold">Dlaczego ta punktacja?</h2>
      <p className="mt-1 mb-3 text-sm text-muted-foreground">
        Każda pozycja pokazuje wynik, maksimum, źródło i pewność. „Brak danych” oznacza, że dane nie zostały jeszcze
        sprawdzone, a nie że lokalizacja jest zła.
      </p>
      <Breakdown site={site} />
    </>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border bg-surface p-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 font-semibold tabular-nums">{value}</dd>
    </div>
  );
}

/** The site card (brief 8.1 "Karta lokalizacji"): score, why, and what still needs checking. */
export function SiteCardPage() {
  const { siteId = "" } = useParams();
  const site = useQuery({
    queryKey: ["site", siteId],
    queryFn: () => unwrap(api.GET("/api/sites/{site_id}", { params: { path: { site_id: siteId } } })),
    retry: (count, error) => (error as { status?: number }).status !== 404 && count < 2,
  });
  const notFound = (site.error as { status?: number } | null)?.status === 404;

  return (
    <PageScroll label="Karta lokalizacji">
      <div className="mx-auto max-w-3xl p-4 md:p-6">
        <Link to="/" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" aria-hidden /> Wszystkie lokalizacje
        </Link>
        {site.isPending && <p className="mt-4 text-sm text-muted-foreground" role="status">Wczytuję kartę…</p>}
        {notFound && (
          <EmptyState icon={MapPinned} title="Nie ma takiej lokalizacji">
            Lista mogła się zmienić po ostatnim przebiegu oceny.
          </EmptyState>
        )}
        {site.isError && !notFound && (
          <p role="alert" className="mt-4 text-sm text-bad">
            Nie udało się wczytać karty lokalizacji.
          </p>
        )}
        {site.data && (
          <div className="mt-4">
            <SiteCard site={site.data} />
          </div>
        )}
      </div>
    </PageScroll>
  );
}
