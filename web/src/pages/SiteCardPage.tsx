import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, MapPinned } from "lucide-react";
import { Link, useParams } from "react-router";

import { api, shouldRetry, unwrap } from "@/api/client";
import { EmptyState } from "@/components/EmptyState";
import { mapsUrl } from "@/components/map/popup";
import { PageScroll } from "@/components/PageScroll";
import { Sheet } from "@/components/check/Sheet";
import { ConnectionCostCalculator } from "@/components/site/ConnectionCostCalculator";
import { ParcelIds } from "@/components/site/ParcelIds";
import { ProjectActions } from "@/components/site/ProjectActions";
import { Protokol } from "@/components/site/Protokol";
import { Ruler } from "@/components/site/Ruler";
import { ToCheck } from "@/components/site/ToCheck";
import { Badge } from "@/components/ui/badge";
import { formatDate, formatHa, formatMW, formatPercent } from "@/lib/format";
import { CONFIDENCE_LABEL, placeLine, type SiteDetail } from "@/lib/sites";

function SiteCard({ site }: { site: SiteDetail }) {
  const parcels = site.parcel_ids.length;
  return (
    <>
      <div className="flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold">Lokalizacja: {formatHa(site.area_ha)}</h1>
          {placeLine(site, true) && <p className="mt-1 text-sm text-muted-foreground">{placeLine(site, true)}</p>}
          <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            Szacowana moc {formatMW(site.estimated_mw)}
            <Badge variant="accent">{CONFIDENCE_LABEL[site.mw_fact.confidence]}</Badge>
            <span className="text-xs">
              powierzchnia × gęstość z ustawień ({formatDate(site.mw_fact.as_of)})
            </span>
          </p>
          <p className="mt-1 text-sm">
            <a
              href={mapsUrl(site.lon, site.lat)}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-accent"
            >
              Pokaż w Mapach Google
            </a>
          </p>
          {parcels > 0 && parcels <= 20 && (
            <p className="mt-1 text-sm">
              <Link
                to={`/sprawdz-dzialke?q=${encodeURIComponent(site.parcel_ids.join("\n"))}`}
                className="text-accent underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-accent"
              >
                {parcels === 1 ? "Sprawdź tę działkę" : "Sprawdź te działki"} z aktualnymi danymi
              </Link>
            </p>
          )}
          <div className="mt-2 flex flex-wrap gap-1.5">
            <Badge variant={site.grid_badge === "Opublikowana moc" ? "ok" : site.grid_badge === "Bliskość sieci" ? "accent" : "neutral"}>
              {site.grid_badge}
            </Badge>
            {site.excluded && <Badge variant="bad">Wykluczona</Badge>}
            {site.large_campus && <Badge variant="accent">Duży kampus: wymaga węzła 220/400 kV</Badge>}
            {site.split_suggested && <Badge variant="warn">Duży teren: rozważ podział</Badge>}
            {site.flags.map((f) => (
              <Badge key={f}>{f}</Badge>
            ))}
          </div>
          <ProjectActions site_id={site.site_id} watched={site.watched} stage={site.stage} project_note={site.project_note} />
        </div>
      </div>

      <ToCheck items={site.items} />

      <section className="mt-6" aria-label="Mapa lokalizacji">
        <Sheet sheet={{ ...site.map, layers: [] }} photoUrl={`/api/sites/${encodeURIComponent(site.site_id)}/orthophoto`} />
      </section>

      <section className="mt-6" aria-label="Wynik">
        <Ruler score={site.score} upside={site.upside_score} dim={site.excluded} />
      </section>

      <dl className="grid grid-cols-3 gap-3">
        <Fact label="Działki" value={`${parcels}`} />
        <Fact label="Kompletność danych" value={formatPercent(site.completeness)} />
        <Fact label="Dane z" value={formatDate(site.scored_at)} />
      </dl>

      <ParcelIds ids={site.parcel_ids} />

      <h2 className="mt-6 text-base font-semibold">Dlaczego ta punktacja?</h2>
      <p className="mt-1 mb-3 text-sm text-muted-foreground">
        Każda pozycja pokazuje wynik, maksimum, źródło i pewność. „Brak danych” oznacza, że dane nie zostały jeszcze
        sprawdzone, a nie że lokalizacja jest zła.
      </p>
      <Protokol site={site} />

      <ConnectionCostCalculator key={site.site_id} estimatedMw={site.estimated_mw} />
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
    retry: (count, error) => shouldRetry(count, error, 2),
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
