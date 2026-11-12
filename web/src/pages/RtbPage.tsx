import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Download, Printer } from "lucide-react";
import { useEffect } from "react";
import { Link, useParams } from "react-router";

import { api, shouldRetry, unwrap } from "@/api/client";
import { Sheet } from "@/components/check/Sheet";
import { Mark, MarkLegend } from "@/components/site/Mark";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDate, formatDateTime, formatHa, formatMW, formatNumber, formatPln } from "@/lib/format";
import {
  RAG_LABEL,
  RAG_VARIANT,
  downloadJson,
  evidenceLabel,
  factValue,
  type RtbExport,
} from "@/lib/rtb";
import { CONFIDENCE_LABEL, markForConfidence, placeLine } from "@/lib/sites";

const SECTION = "mt-6 break-inside-avoid-page";
const TH = "py-1.5 pr-3 text-left text-xs font-medium text-muted-foreground";
const TD = "border-t py-1.5 pr-3 align-top";

/** "osm_power, 12.10.2026": the source and date that stand behind a statement. */
function Provenance({ source, asOf }: { source: string | null; asOf: string | null }) {
  if (!source && !asOf) return <span className="text-muted-foreground">brak źródła</span>;
  return (
    <span className="text-xs text-muted-foreground">
      {source ?? "brak źródła"}
      {asOf ? `, ${formatDate(asOf)}` : ""}
    </span>
  );
}

function Section({ title, children, id }: { title: string; children: React.ReactNode; id: string }) {
  return (
    <section className={SECTION} aria-labelledby={id}>
      <h2 id={id} className="border-b-2 border-ink pb-1 text-base font-semibold">
        {title}
      </h2>
      <div className="mt-2">{children}</div>
    </section>
  );
}

function RagTable({ rtb }: { rtb: RtbExport }) {
  return (
    <>
      <p className="mb-2 text-xs text-muted-foreground">{rtb.status_note_pl}</p>
      <table className="w-full text-sm" data-testid="rag-table">
        <thead>
          <tr>
            <th className={TH}>Obszar</th>
            <th className={TH}>Status</th>
            <th className={TH}>Dlaczego (fakt, źródło, data)</th>
          </tr>
        </thead>
        <tbody>
          {rtb.workstreams.map((w) => (
            <tr key={w.key} data-testid={`rag-${w.key}`} className="break-inside-avoid">
              <td className={`${TD} font-medium`}>{w.label_pl}</td>
              <td className={TD}>
                <Badge variant={RAG_VARIANT[w.status]}>{RAG_LABEL[w.status]}</Badge>
              </td>
              <td className={TD}>
                <ul className="space-y-1">
                  {w.reasons.map((r, i) => (
                    <li key={i}>
                      {r.text_pl} <Provenance source={r.source_id} asOf={r.as_of} />
                    </li>
                  ))}
                </ul>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function RedFlags({ rtb }: { rtb: RtbExport }) {
  if (rtb.red_flags.length === 0) {
    return <p className="text-sm">Brak uruchomionych kar ani warunków wykluczających w zapisanej punktacji.</p>;
  }
  return (
    <table className="w-full text-sm" data-testid="red-flags">
      <thead>
        <tr>
          <th className={TH}>Rodzaj</th>
          <th className={TH}>Co</th>
          <th className={TH}>Punkty</th>
          <th className={TH}>Źródło</th>
        </tr>
      </thead>
      <tbody>
        {rtb.red_flags.map((f) => (
          <tr key={`${f.kind}-${f.key}`} className="break-inside-avoid">
            <td className={TD}>
              <Badge variant={f.kind === "exclusion" ? "bad" : "warn"}>{f.kind === "exclusion" ? "Wyklucza" : "Kara"}</Badge>
            </td>
            <td className={TD}>
              <span className="font-medium">{f.label_pl}</span>
              <span className="block text-muted-foreground">{f.reason_pl}</span>
            </td>
            <td className={`${TD} tabular-nums whitespace-nowrap`}>{f.points > 0 ? `−${formatNumber(f.points, 0)} pkt` : "—"}</td>
            <td className={TD}>
              <Provenance source={f.source_id} asOf={f.as_of} />
              {f.confidence && <span className="block text-xs text-muted-foreground">{CONFIDENCE_LABEL[f.confidence as "verified"] ?? f.confidence}</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PowerEvidenceBlock({ rtb }: { rtb: RtbExport }) {
  const e = rtb.power_evidence;
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm" data-testid="evidence-block">
      <dt className="text-muted-foreground">Poziom dowodu</dt>
      <dd className="font-medium">
        {evidenceLabel(e.level)}
        {!e.recorded && <span className="font-normal text-muted-foreground"> (nic nie wpisano: domyślnie)</span>}
      </dd>
      <dt className="text-muted-foreground">Data dokumentu</dt>
      <dd>{formatDate(e.document_date)}</dd>
      <dt className="text-muted-foreground">Warunki ważne do</dt>
      <dd>{formatDate(e.conditions_expiry)}</dd>
      <dt className="text-muted-foreground">Dokument</dt>
      <dd>
        {e.attachment_url ? (
          <a href={e.attachment_url} target="_blank" rel="noopener noreferrer" className="break-all text-accent underline underline-offset-2">
            {e.attachment_url}
          </a>
        ) : (
          "brak linku"
        )}
      </dd>
      <dt className="text-muted-foreground">Źródło</dt>
      <dd className="text-xs text-muted-foreground">
        wpisane ręcznie przez zespół{e.updated_at ? `, ${formatDate(e.updated_at)}` : ""}
      </dd>
    </dl>
  );
}

function ConnectionCostBlock({ rtb }: { rtb: RtbExport }) {
  const c = rtb.connection_cost;
  return (
    <>
      <p className="mb-2 flex flex-wrap items-center gap-2 text-sm">
        Dla mocy projektu {formatMW(c.mw)}. Podstawa: {c.legal_basis}.
        {!c.verified && <Badge variant="warn">wymaga weryfikacji prawnej</Badge>}
      </p>
      <table className="w-full text-sm" data-testid="connection-cost">
        <thead>
          <tr>
            <th className={TH}>Pozycja</th>
            <th className={TH}>Kwota</th>
            <th className={TH}>Zasada</th>
            <th className={TH}>Źródło</th>
          </tr>
        </thead>
        <tbody>
          {c.lines.map((l) => (
            <tr key={l.key}>
              <td className={TD}>{l.label_pl}</td>
              <td className={`${TD} tabular-nums whitespace-nowrap`}>{formatPln(l.pln)}</td>
              <td className={TD}>{l.basis_pl}</td>
              <td className={TD}>
                <Provenance source={l.source_id} asOf={l.as_of} />
                <span className="block text-xs text-muted-foreground">{CONFIDENCE_LABEL[l.confidence]}</span>
              </td>
            </tr>
          ))}
          <tr className="font-medium">
            <td className={TD}>Zamrożone (zaliczka i zabezpieczenie)</td>
            <td className={`${TD} tabular-nums`}>{formatPln(c.frozen_pln)}</td>
            <td className={TD} colSpan={2} />
          </tr>
          <tr className="font-medium">
            <td className={TD}>Razem z opłatą za wniosek</td>
            <td className={`${TD} tabular-nums`}>{formatPln(c.total_pln)}</td>
            <td className={TD} colSpan={2} />
          </tr>
        </tbody>
      </table>
      <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
        {c.caveats_pl.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ul>
    </>
  );
}

function FactsTable({ rtb }: { rtb: RtbExport }) {
  return (
    <>
      <div className="mb-2">
        <MarkLegend />
      </div>
      <table className="w-full text-sm" data-testid="facts-table">
        <thead>
          <tr>
            <th className={TH} aria-label="Pewność" />
            <th className={TH}>Fakt</th>
            <th className={TH}>Wartość</th>
            <th className={TH}>Źródło i data</th>
          </tr>
        </thead>
        <tbody>
          {rtb.facts.map((f) => (
            <tr key={f.key} className="break-inside-avoid" data-confidence={f.confidence}>
              <td className={TD}>
                <Mark kind={markForConfidence(f.confidence)} />
              </td>
              <td className={TD}>{f.label_pl}</td>
              <td className={`${TD} tabular-nums`}>{factValue(f, (n) => formatNumber(n, 1))}</td>
              <td className={TD}>
                <Provenance source={f.source_id} asOf={f.as_of} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function RtbDocument({ rtb }: { rtb: RtbExport }) {
  const { site } = rtb;
  const place = placeLine(site, true);
  return (
    <article className="mx-auto max-w-4xl p-4 text-foreground md:p-6 print:max-w-none print:p-0" data-testid="rtb">
      <header className="border-b-2 border-ink pb-3">
        <p className="text-xs tracking-wide text-muted-foreground uppercase">Aneks RTB · DC Land Hunter</p>
        <h1 className="mt-1 text-xl font-semibold">
          Lokalizacja {formatHa(site.area_ha)}
          {place ? `, ${place}` : ""}
        </h1>
        <p className="mt-1 text-sm">
          Moc projektu {formatMW(site.project_mw)} · pojemność terenu {formatMW(site.estimated_mw)} (szacunek) · punktacja{" "}
          {formatNumber(site.score, 0)} z 100 · działki: {rtb.parcel_ids.length}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Dane z oceny {formatDate(rtb.scored_at)} · aneks wygenerowano {formatDateTime(rtb.generated_at)} · id {site.site_id}
        </p>
      </header>

      <Section id="rtb-map" title="Mapa: teren, linie 110 kV i stacje">
        <Sheet sheet={rtb.map} />
        <p className="mt-1 text-xs text-muted-foreground">{rtb.map_note_pl}</p>
      </Section>

      <Section id="rtb-flags" title="Czerwone flagi">
        <RedFlags rtb={rtb} />
      </Section>

      <Section id="rtb-rag" title="Obszary robocze: propozycja statusów">
        <RagTable rtb={rtb} />
      </Section>

      <Section id="rtb-evidence" title="Dowód mocy">
        <PowerEvidenceBlock rtb={rtb} />
      </Section>

      <Section id="rtb-cost" title="Koszt przyłączenia">
        <ConnectionCostBlock rtb={rtb} />
      </Section>

      <Section id="rtb-rcn" title="Ceny transakcyjne (RCN)">
        {rtb.price_comparables ? (
          <p className="text-sm" data-testid="rcn-present">
            Porównywalne transakcje RCN są w pliku JSON (pole price_comparables).
          </p>
        ) : (
          <p className="text-sm" data-testid="rcn-empty">
            Brak danych RCN
          </p>
        )}
      </Section>

      <Section id="rtb-facts" title="Tabela faktów">
        <FactsTable rtb={rtb} />
      </Section>
    </article>
  );
}

/**
 * The RTB annex of one site: a page made to be printed (A4, no navigation) and saved as PDF with the browser's
 * print dialog. The JSON download is the same export from `GET /api/sites/{id}/rtb`.
 */
export function RtbPage() {
  const { siteId = "" } = useParams();
  const rtb = useQuery({
    queryKey: ["rtb", siteId],
    queryFn: () => unwrap(api.GET("/api/sites/{site_id}/rtb", { params: { path: { site_id: siteId } } })),
    retry: (count, error) => shouldRetry(count, error, 2),
  });
  const notFound = (rtb.error as { status?: number } | null)?.status === 404;

  useEffect(() => {
    document.title = "Aneks RTB · DC Land Hunter";
  }, []);

  return (
    <div className="rtb-page min-h-full bg-background">
      <div className="mx-auto flex max-w-4xl flex-wrap items-center gap-2 p-4 pb-0 md:px-6 print:hidden">
        <Link to={`/lokalizacje/${encodeURIComponent(siteId)}`} className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" aria-hidden /> Karta lokalizacji
        </Link>
        <span className="flex-1" />
        <Button size="sm" disabled={!rtb.data} onClick={() => window.print()}>
          <Printer aria-hidden />
          Pobierz PDF
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!rtb.data}
          onClick={() => rtb.data && downloadJson(`aneks-rtb-${siteId}.json`, rtb.data)}
        >
          <Download aria-hidden />
          Pobierz JSON
        </Button>
      </div>
      {rtb.isPending && (
        <p className="p-4 text-sm text-muted-foreground md:px-6" role="status">
          Przygotowuję aneks…
        </p>
      )}
      {notFound && <p className="p-4 text-sm md:px-6">Nie ma takiej lokalizacji. Lista mogła się zmienić po ostatnim przebiegu oceny.</p>}
      {rtb.isError && !notFound && (
        <p role="alert" className="p-4 text-sm text-bad md:px-6">
          Nie udało się wczytać aneksu RTB.
        </p>
      )}
      {rtb.data && <RtbDocument rtb={rtb.data} />}
    </div>
  );
}
