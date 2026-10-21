import { CircleAlert, CircleCheck, Clock, LoaderCircle, Lock, ServerCrash } from "lucide-react";

import type { JobItem, OpsJobs } from "@/api/client";
import { EmptyState } from "@/components/EmptyState";
import { PageScroll } from "@/components/PageScroll";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { useAuth } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import { useOpsJobs } from "@/lib/ops";

const STATUS: Record<string, { label: string; variant: BadgeProps["variant"] }> = {
  pending: { label: "Oczekuje", variant: "neutral" },
  running: { label: "W toku", variant: "accent" },
  done: { label: "Zakończone", variant: "ok" },
  failed: { label: "Błąd", variant: "bad" },
};

const KIND: Record<string, string> = {
  search: "Wyszukiwanie",
  discover: "Weryfikacja źródeł",
  plan_searches: "Plan nocny",
};

function Counts({ counts }: { counts: OpsJobs["counts"] }) {
  const items = [
    { key: "failed", n: counts.failed, note: "ostatnie 24 h" },
    { key: "done", n: counts.done, note: "ostatnie 24 h" },
    { key: "running", n: counts.running, note: "teraz" },
    { key: "pending", n: counts.pending, note: "w kolejce" },
  ] as const;
  return (
    <div role="group" aria-label="Podsumowanie zadań" className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
      {items.map(({ key, n, note }) => (
        <Card key={key} className="p-3">
          <div className={n > 0 && key === "failed" ? "text-2xl font-semibold text-bad" : "text-2xl font-semibold"}>{n}</div>
          <div className="text-xs text-muted-foreground">
            {STATUS[key]!.label} · {note}
          </div>
        </Card>
      ))}
    </div>
  );
}

function FailingPowiats({ rows }: { rows: OpsJobs["failing_powiats"] }) {
  if (rows.length === 0) {
    return (
      <p className="mt-6 flex items-center gap-2 text-sm text-ok">
        <CircleCheck className="size-4" aria-hidden /> Ostatnie wyszukiwanie każdego powiatu zakończyło się sukcesem.
      </p>
    );
  }
  return (
    <section className="mt-6" aria-labelledby="failing-heading">
      <h2 id="failing-heading" className="flex items-center gap-2 text-sm font-semibold text-bad">
        <CircleAlert className="size-4" aria-hidden /> Powiaty, których ostatnie wyszukiwanie się nie udało ({rows.length})
      </h2>
      <p className="mt-1 text-xs text-muted-foreground">Ich lokalizacje nie są odświeżane, dopóki kolejne wyszukiwanie nie zakończy się sukcesem.</p>
      <ul className="mt-2 flex flex-col gap-2">
        {rows.map((p) => (
          <li key={p.teryt} data-testid="failing-powiat">
            <Card className="p-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-medium">
                  {p.name ?? "Powiat"} <span className="font-mono text-xs text-muted-foreground">{p.teryt}</span>
                </span>
                <span className="text-xs text-muted-foreground">{formatDateTime(p.failed_at)}</span>
              </div>
              {p.error && <p className="mt-1 break-words font-mono text-xs text-bad">{p.error}</p>}
            </Card>
          </li>
        ))}
      </ul>
    </section>
  );
}

function RecentJobs({ jobs }: { jobs: JobItem[] }) {
  if (jobs.length === 0) return <EmptyState icon={Clock} title="Brak zadań" />;
  return (
    <section className="mt-6" aria-labelledby="recent-heading">
      <h2 id="recent-heading" className="text-sm font-semibold">
        Ostatnie zadania
      </h2>
      <Card className="mt-2 overflow-x-auto">
        <table className="w-full text-sm">
          <caption className="sr-only">Ostatnie zadania worker-a</caption>
          <thead className="border-b bg-surface-2 text-left text-xs text-muted-foreground">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">Status</th>
              <th scope="col" className="px-3 py-2 font-medium">Zadanie</th>
              <th scope="col" className="px-3 py-2 font-medium">Utworzone</th>
              <th scope="col" className="px-3 py-2 font-medium">Zakończone</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => {
              const s = STATUS[j.status] ?? { label: j.status, variant: "neutral" as const };
              return (
                <tr key={j.id} className="border-b align-top last:border-0" data-testid="job-row">
                  <td className="px-3 py-2.5">
                    <Badge variant={s.variant}>{s.label}</Badge>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="font-medium">
                      {KIND[j.kind] ?? j.kind}
                      {j.powiat && <span className="font-normal text-muted-foreground"> · {j.powiat_name ?? j.powiat}</span>}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      #{j.id}
                      {j.requested_by && ` · ${j.requested_by}`}
                    </div>
                    {j.error && <p className="mt-1 break-words font-mono text-xs text-bad">{j.error}</p>}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-muted-foreground">{formatDateTime(j.created_at)}</td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-muted-foreground">{formatDateTime(j.finished_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
    </section>
  );
}

export function JobsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const { data, isPending, isError } = useOpsJobs(isAdmin);

  return (
    <PageScroll label="Zadania">
      <div className="mx-auto max-w-6xl p-4 md:p-6">
        <h1 className="text-xl font-semibold">Zadania</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Co zrobił worker: nocne wyszukiwania powiatów i weryfikacja źródeł. Czerwony licznik w menu pojawia się, gdy
          któryś powiat ma nieudane wyszukiwanie albo inne zadanie nie powiodło się w ciągu doby.
        </p>

        {!isAdmin && (
          <EmptyState icon={Lock} title="Brak uprawnień">
            Ten widok jest dostępny tylko dla administratora.
          </EmptyState>
        )}
        {isAdmin && isPending && <EmptyState icon={LoaderCircle} title="Wczytuję zadania…" className="[&_svg]:animate-spin" />}
        {isAdmin && isError && (
          <EmptyState icon={ServerCrash} title="Nie udało się pobrać zadań" tone="bad">
            API nie odpowiada. Sprawdź, czy usługa „api” działa (docs/RUNBOOK.md).
          </EmptyState>
        )}
        {data && (
          <>
            <Counts counts={data.counts} />
            <FailingPowiats rows={data.failing_powiats} />
            <RecentJobs jobs={data.recent} />
          </>
        )}
      </div>
    </PageScroll>
  );
}
