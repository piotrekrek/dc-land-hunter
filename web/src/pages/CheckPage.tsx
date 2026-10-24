import { ArrowLeft, CircleAlert, MapPinned, RotateCw } from "lucide-react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import type { CheckOut, CheckSiteSummary } from "@/api/client";
import { CheckResult } from "@/components/check/CheckResult";
import { Ledger, ProgressBar } from "@/components/check/Ledger";
import { EmptyState } from "@/components/EmptyState";
import { PageScroll } from "@/components/PageScroll";
import { Ruler } from "@/components/site/Ruler";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/api/client";
import { progressOf, runningStep, useCheck, useCheckSite, useStartCheck, useTabTitle } from "@/lib/check";
import { formatHa, formatMW } from "@/lib/format";
import { cn } from "@/lib/utils";

function Running({ check }: { check: CheckOut }) {
  const { done, total } = progressOf(check.steps);
  const now = runningStep(check.steps);
  const many = check.parcels.length > 1;
  return (
    <section aria-labelledby="run-h">
      <h1 id="run-h" tabIndex={-1} className="text-xl font-semibold">
        Sprawdzam {many ? `działki (${check.parcels.length})` : "działkę"} <span className="font-cond font-medium break-all">{check.parcels.join(", ")}</span>
      </h1>
      <div className="mt-4 max-w-xl">
        <ProgressBar steps={check.steps} estimateSeconds={check.estimate_seconds} />
      </div>
      {/* One polite announcement per step change, not one per second. */}
      <p className="sr-only" role="status" aria-live="polite">
        {now ? `${now.label_pl}. Krok ${done + 1} z ${total}.` : "Czekam na rozpoczęcie."}
      </p>
      <p className="mt-3 max-w-xl text-sm text-muted-foreground">
        Możesz wyjść z tej strony. Wynik zapisze się na liście „Ostatnie sprawdzenia”.
        {check.status === "pending" && " Sprawdzenie czeka w kolejce na wolnego pracownika."}
      </p>
      <div className="mt-5 max-w-3xl">
        <Ledger steps={check.steps} />
      </div>
    </section>
  );
}

function ParcelList({ sites, selected, onSelect }: { sites: CheckSiteSummary[]; selected: number; onSelect: (n: number) => void }) {
  return (
    <ul className="m-0 mb-6 list-none border-t border-ink p-0" data-testid="parcel-list">
      {sites.map((s) => (
        <li key={s.n} className="border-b">
          <button
            type="button"
            aria-pressed={selected === s.n}
            onClick={() => onSelect(s.n)}
            className={cn("grid w-full gap-x-4 gap-y-1 py-2.5 text-left text-sm hover:bg-surface-2 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)_minmax(150px,1fr)]", selected === s.n && "bg-accent-soft")}
          >
            <span>
              <span className="font-cond font-medium break-all">{s.parcel_id}</span>
              <span className="block text-xs text-muted-foreground">
                {s.commune}, obręb {s.obreb} · {formatHa(s.area_ha)} · {formatMW(s.estimated_mw)}
              </span>
            </span>
            <span>{s.verdict.text_pl}</span>
            <Ruler score={s.score} upside={s.upside_score} dim={s.excluded} className="[&_span.text-4xl]:text-xl [&>div:first-child>span:last-child]:hidden [&>div:last-child]:mb-5" />
          </button>
        </li>
      ))}
    </ul>
  );
}

/** A check: first its progress, then the result, in the same place so the address can be shared. */
export function CheckPage() {
  const { checkId = "" } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const check = useCheck(checkId);
  const again = useStartCheck();
  const data = check.data;
  const n = Math.max(0, Number(params.get("n") ?? 0) || 0);
  const finished = data && (data.status === "done" || data.status === "failed");
  const selected = data?.sites.find((s) => s.n === n) ?? data?.sites[0];
  const site = useCheckSite(checkId, selected?.n ?? 0, Boolean(finished && selected));
  const { done, total } = data ? progressOf(data.steps) : { done: 0, total: 0 };

  useTabTitle(
    !data
      ? null
      : data.status === "done"
        ? `Gotowe: ${selected?.verdict.text_pl ?? "sprawdzenie"}`
        : data.status === "failed"
          ? "Sprawdzenie nie powiodło się"
          : `(${done}/${total}) Sprawdzam…`,
  );

  const notFound = check.error instanceof ApiError && check.error.status === 404;
  const recheck = () => again.mutate({ parcels: (data?.parcels ?? []).filter((p) => !p.includes(" N, ")) }, { onSuccess: ({ id }) => navigate(`/sprawdz-dzialke/${id}`) });

  return (
    <PageScroll label="Wynik sprawdzenia">
      <div className="mx-auto max-w-6xl p-4 md:p-6">
        <Link to="/sprawdz-dzialke" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" aria-hidden /> Sprawdź inną działkę
        </Link>
        <div className="mt-4">
          {check.isPending && <p className="text-sm text-muted-foreground" role="status">Wczytuję sprawdzenie…</p>}
          {notFound && (
            <EmptyState icon={MapPinned} title="Nie ma takiego sprawdzenia">
              Adres mógł zostać źle skopiowany.
            </EmptyState>
          )}
          {check.isError && !notFound && (
            <p role="alert" className="text-sm text-bad">
              Nie udało się wczytać sprawdzenia.
            </p>
          )}

          {data && (data.status === "pending" || data.status === "running") && <Running check={data} />}

          {data && finished && (
            <>
              {data.status === "failed" && (
                <div role="alert" className="mb-6 flex gap-3 border border-bad p-4" data-testid="failed">
                  <CircleAlert className="mt-0.5 size-5 shrink-0 text-bad" aria-hidden />
                  <div>
                    <p className="font-medium">Sprawdzenie nie powiodło się{data.sites.length > 0 ? " do końca" : ""}.</p>
                    {data.error && <p className="mt-1 text-sm break-words">{data.error}</p>}
                    {data.sites.length > 0 && <p className="mt-1 text-sm text-muted-foreground">Poniżej wynik dla działek, które zdążyły się sprawdzić.</p>}
                  </div>
                </div>
              )}
              {data.sites.length > 1 && <ParcelList sites={data.sites} selected={selected?.n ?? 0} onSelect={(i) => setParams({ n: String(i) })} />}
              {selected && site.data && <CheckResult site={site.data} />}
              {selected && site.isPending && <p className="text-sm text-muted-foreground" role="status">Wczytuję wynik…</p>}
              {selected && site.isError && (
                <p role="alert" className="text-sm text-bad">
                  Nie udało się wczytać wyniku.
                </p>
              )}
              {data.parcels.some((p) => !p.includes(" N, ")) && (
                <div className="mt-8 flex flex-wrap items-center gap-3 border-t pt-4">
                  <Button type="button" variant="outline" onClick={recheck} disabled={again.isPending}>
                    <RotateCw aria-hidden /> Sprawdź ponownie
                  </Button>
                  <span className="text-xs text-muted-foreground">
                    Użyje aktualnych danych. To, co się już udało pobrać, zostanie wczytane z pamięci podręcznej.
                  </span>
                  {again.error instanceof Error && (
                    <span role="alert" className="text-sm text-bad">
                      {again.error.message}
                    </span>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </PageScroll>
  );
}
