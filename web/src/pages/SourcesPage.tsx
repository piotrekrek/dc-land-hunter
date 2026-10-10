import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { CircleCheck, CircleX, Database, LoaderCircle, Minus, Play, ServerCrash, TriangleAlert } from "lucide-react";

import { api, unwrap, type SourceOut } from "@/api/client";
import { EmptyState } from "@/components/EmptyState";
import { PageScroll } from "@/components/PageScroll";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useAuth } from "@/lib/auth";
import { formatDate, formatDateTime } from "@/lib/format";

type DiscoveryStatus = NonNullable<SourceOut["discovery_status"]>;

const STATUS: Record<DiscoveryStatus | "unknown", { label: string; variant: BadgeProps["variant"]; icon: typeof CircleCheck }> = {
  verified: { label: "Zweryfikowane", variant: "ok", icon: CircleCheck },
  partial: { label: "Do potwierdzenia", variant: "warn", icon: TriangleAlert },
  failed: { label: "Niedostępne", variant: "bad", icon: CircleX },
  not_checked: { label: "Nie dotyczy", variant: "neutral", icon: Minus },
  unknown: { label: "Nie sprawdzono", variant: "neutral", icon: Minus },
};

const PLAN: Record<SourceOut["plan"], string> = { mvp: "MVP", v2: "Wersja 2", manual: "Tylko link" };

const CADENCE: Record<string, string> = {
  daily: "codziennie",
  weekly: "co tydzień",
  monthly: "co miesiąc",
  static: "statyczne",
  none: "—",
};

function StatusBadge({ source }: { source: SourceOut }) {
  const s = STATUS[source.plan === "mvp" ? (source.discovery_status ?? "unknown") : "not_checked"];
  const Icon = s.icon;
  return (
    <Badge variant={s.variant}>
      <Icon aria-hidden /> {s.label}
    </Badge>
  );
}

function LastRun({ source }: { source: SourceOut }) {
  if (source.state?.stale) return <span className="text-warn">nieaktualne od {formatDate(source.state.last_success_at)}</span>;
  return <>{formatDate(source.state?.last_success_at)}</>;
}

const REFRESH_DELAYS_MS = [15_000, 45_000, 90_000];

function RunNow() {
  const { user } = useAuth();
  const client = useQueryClient();
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const run = useMutation({
    mutationFn: async () => {
      const { data, response } = await api.POST("/api/sources/discover", { body: { sources: [] } });
      if (!data) throw new Error(`API ${response.status}`);
      return data;
    },
  });
  const { mutate, reset } = run;

  // Clear pending refresh timers when the page is left.
  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  const start = () =>
    mutate(undefined, {
      onSuccess: () => {
        // The worker needs a moment; refresh the table in the background, then allow another run.
        timers.current.forEach(clearTimeout);
        timers.current = REFRESH_DELAYS_MS.map((delay, i) =>
          setTimeout(() => {
            void client.invalidateQueries({ queryKey: ["sources"] });
            if (i === REFRESH_DELAYS_MS.length - 1) reset();
          }, delay),
        );
      },
    });
  if (user?.role !== "admin") return null;
  return (
    <div className="mt-4 flex flex-wrap items-center gap-3">
      <Button variant="outline" size="sm" onClick={start} disabled={run.isPending || run.isSuccess}>
        {run.isPending ? <LoaderCircle className="animate-spin" aria-hidden /> : <Play aria-hidden />}
        Uruchom teraz
      </Button>
      {run.isSuccess && (
        <span role="status" className="text-sm text-muted-foreground">
          Zadanie #{run.data.id} dodane do kolejki. Wynik pojawi się w tabeli za kilkadziesiąt sekund.
        </span>
      )}
      {run.isError && (
        <span role="alert" className="text-sm text-bad">
          Nie udało się dodać zadania.
        </span>
      )}
    </div>
  );
}

export function SourcesPage() {
  const { data, isPending, isError } = useQuery({
    queryKey: ["sources"],
    queryFn: () => unwrap(api.GET("/api/sources")),
  });

  return (
    <PageScroll label="Źródła danych">
      <div className="mx-auto max-w-6xl p-4 md:p-6">
        <h1 className="text-xl font-semibold">Źródła danych</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Stan każdego źródła: wynik weryfikacji (discovery), ostatnie udane pobranie i częstotliwość odświeżania.
          Usługi publiczne często blokują adresy spoza Polski, więc weryfikacja musi być uruchamiana z serwera w Polsce.
        </p>

        <RunNow />

        {isPending && (
          <EmptyState icon={LoaderCircle} title="Wczytuję listę źródeł…" className="[&_svg]:animate-spin" />
        )}
        {isError && (
          <EmptyState icon={ServerCrash} title="Nie udało się pobrać listy źródeł" tone="bad">
            API nie odpowiada. Sprawdź, czy usługa „api” działa (docs/RUNBOOK.md).
          </EmptyState>
        )}
        {data && <SourcesBody sources={data.sources} database={data.database} />}
      </div>
    </PageScroll>
  );
}

function Summary({ sources }: { sources: SourceOut[] }) {
  const mvp = sources.filter((s) => s.plan === "mvp");
  const count = (st: DiscoveryStatus | null) => mvp.filter((s) => (s.discovery_status ?? null) === st).length;
  const items = [
    { key: "verified", n: count("verified") },
    { key: "partial", n: count("partial") },
    { key: "failed", n: count("failed") },
    { key: "unknown", n: count(null) },
  ] as const;
  return (
    <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="Podsumowanie źródeł MVP">
      {items.map(({ key, n }) => (
        <Card key={key} className="p-3">
          <div className="text-2xl font-semibold">{n}</div>
          <div className="text-xs text-muted-foreground">{STATUS[key].label}</div>
        </Card>
      ))}
    </div>
  );
}

function SourcesBody({ sources, database }: { sources: SourceOut[]; database: boolean }) {
  const neverChecked = sources.filter((s) => s.plan === "mvp").every((s) => !s.discovery_status);
  return (
    <>
      {!database && (
        <div role="alert" className="mt-4 rounded-md border border-warn/40 bg-warn-soft px-3 py-2 text-sm text-warn">
          Baza danych jest niedostępna – pokazuję tylko konfigurację źródeł, bez historii pobrań.
        </div>
      )}
      {neverChecked && (
        <div className="mt-4 rounded-md border bg-surface-2 px-3 py-2 text-sm text-muted-foreground">
          Weryfikacja źródeł nie została jeszcze uruchomiona. Na maszynie w Polsce wykonaj{" "}
          <code className="font-mono text-foreground">uv run dclh discover --write-docs</code>.
        </div>
      )}
      <Summary sources={sources} />

      {/* Desktop table */}
      <Card className="mt-4 hidden overflow-x-auto md:block">
        <table className="w-full text-sm">
          <caption className="sr-only">Lista źródeł danych</caption>
          <thead className="border-b bg-surface-2 text-left text-xs text-muted-foreground">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">Status</th>
              <th scope="col" className="px-3 py-2 font-medium">Źródło</th>
              <th scope="col" className="px-3 py-2 font-medium">Zastosowanie</th>
              <th scope="col" className="px-3 py-2 font-medium">Plan</th>
              <th scope="col" className="px-3 py-2 font-medium">Odświeżanie</th>
              <th scope="col" className="px-3 py-2 font-medium">Ostatni sukces</th>
              <th scope="col" className="px-3 py-2 font-medium">Weryfikacja</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => (
              <tr key={s.id} className="border-b last:border-0 align-top" data-testid="source-row">
                <td className="px-3 py-2.5"><StatusBadge source={s} /></td>
                <td className="px-3 py-2.5">
                  <div className="font-medium">{s.name}</div>
                  <div className="font-mono text-xs text-muted-foreground">
                    {s.id}
                    {s.core && <span className="ml-2 font-sans text-accent-soft-foreground">kluczowe</span>}
                  </div>
                </td>
                <td className="px-3 py-2.5 text-muted-foreground">{s.used_for}</td>
                <td className="px-3 py-2.5">
                  {s.manual_url ? (
                    <a className="text-accent underline-offset-4 hover:underline" href={s.manual_url} target="_blank" rel="noreferrer">
                      {PLAN[s.plan]}
                    </a>
                  ) : (
                    PLAN[s.plan]
                  )}
                </td>
                <td className="px-3 py-2.5 text-muted-foreground">{CADENCE[s.cadence] ?? s.cadence}</td>
                <td className="px-3 py-2.5 text-muted-foreground"><LastRun source={s} /></td>
                <td className="px-3 py-2.5 text-muted-foreground">{formatDateTime(s.discovery_checked_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {/* Mobile cards */}
      <ul className="mt-4 flex flex-col gap-2 md:hidden">
        {sources.map((s) => (
          <li key={s.id}>
            <Card className="p-3">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="font-medium">{s.name}</div>
                  <div className="font-mono text-xs text-muted-foreground">{s.id}</div>
                </div>
                <StatusBadge source={s} />
              </div>
              <p className="mt-2 text-sm text-muted-foreground">{s.used_for}</p>
              <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
                <Badge>{PLAN[s.plan]}</Badge>
                <Badge>{CADENCE[s.cadence] ?? s.cadence}</Badge>
                {s.core && <Badge variant="accent">kluczowe</Badge>}
              </div>
            </Card>
          </li>
        ))}
      </ul>
      {sources.length === 0 && <EmptyState icon={Database} title="Brak skonfigurowanych źródeł" />}
    </>
  );
}
