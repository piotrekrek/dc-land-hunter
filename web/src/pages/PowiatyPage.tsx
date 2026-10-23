import { CircleAlert, CircleCheck, Clock, ListOrdered, LoaderCircle, Search, ServerCrash } from "lucide-react";
import { useState } from "react";

import type { PowiatItem } from "@/api/client";
import { EmptyState } from "@/components/EmptyState";
import { PageScroll } from "@/components/PageScroll";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import { MIN_QUERY, activeQuery, matchesQuery, usePowiaty, useSavePriority } from "@/lib/powiaty";
import { cn } from "@/lib/utils";

type Filter = "all" | "unchecked" | "checked";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "Wszystkie" },
  { key: "unchecked", label: "Niesprawdzone" },
  { key: "checked", label: "Sprawdzone" },
];

interface Group {
  key: string;
  name: string;
  items: PowiatItem[];
}

/** The API sorts by województwo name, so a group is a run of items with the same one. */
function groupByVoivodeship(items: PowiatItem[]): Group[] {
  const groups: Group[] = [];
  for (const item of items) {
    const key = item.voivodeship_teryt ?? "";
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.items.push(item);
    else groups.push({ key, name: item.voivodeship_name ?? "Inne", items: [item] });
  }
  return groups;
}

function sameSet(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
  return a.size === b.size && [...a].every((t) => b.has(t));
}

function StatusBadge({ item }: { item: PowiatItem }) {
  if (item.checked) return <Badge variant="ok">Sprawdzony</Badge>;
  if (item.status === "failed") return <Badge variant="bad">Błąd wyszukiwania</Badge>;
  return <Badge>Niesprawdzony</Badge>;
}

function PowiatRow({ item, selected, onToggle }: { item: PowiatItem; selected: boolean; onToggle: () => void }) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-3 py-2 last:border-0" data-testid="powiat-row">
      <div className="min-w-0 flex-1">
        <span className="font-medium">{item.name}</span> <span className="font-mono text-xs text-muted-foreground">{item.teryt}</span>
        {item.error && !item.checked && <p className="mt-0.5 break-words font-mono text-xs text-bad">{item.error}</p>}
      </div>
      <StatusBadge item={item} />
      <span className="w-36 text-xs text-muted-foreground">
        {item.checked ? `${formatDateTime(item.last_ok_at)} · ${item.sites ?? 0} lok.` : item.last_attempt_at ? `próba: ${formatDateTime(item.last_attempt_at)}` : "—"}
      </span>
      {item.checked ? (
        <span className="w-28" />
      ) : (
        <Button
          type="button"
          size="sm"
          variant={selected ? "default" : "outline"}
          aria-pressed={selected}
          aria-label={`Priorytet: ${item.name}`}
          className="w-28"
          onClick={onToggle}
        >
          {selected ? "Priorytet ✓" : "Priorytet"}
        </Button>
      )}
    </li>
  );
}

export function PowiatyPage() {
  const { data, isPending, isError } = usePowiaty();
  const save = useSavePriority();
  const [staged, setStaged] = useState<ReadonlySet<string> | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  const [rawQuery, setRawQuery] = useState("");
  const query = activeQuery(rawQuery);

  const items = data?.items ?? [];
  const saved = new Set(items.filter((i) => i.priority).map((i) => i.teryt));
  const selected = staged ?? saved;
  const dirty = staged !== null && !sameSet(staged, saved);
  const checkedCount = items.filter((i) => i.checked).length;
  const queue = items.filter((i) => selected.has(i.teryt));
  const visible = items.filter(
    (i) => (filter === "all" || (filter === "checked") === i.checked) && (query === null || matchesQuery(i, query)),
  );
  const byCode = new Map(items.map((i) => [i.teryt, i]));
  const nextUp = (data?.next_up ?? []).flatMap((t) => byCode.get(t) ?? []);
  const groups = groupByVoivodeship(visible);

  const toggle = (teryt: string) => {
    const next = new Set(selected);
    if (!next.delete(teryt)) next.add(teryt);
    setStaged(sameSet(next, saved) ? null : next);
  };

  const onSave = () => save.mutate([...selected], { onSuccess: () => setStaged(null) });

  return (
    <PageScroll label="Powiaty">
      <div className="mx-auto max-w-5xl p-4 md:p-6">
        <h1 className="text-xl font-semibold">Powiaty</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Powiat jest „sprawdzony”, gdy ma udane wyszukiwanie. Zaznacz „Priorytet” przy powiatach, które mają być przeszukane
          najpierw, i zapisz listę. Nocny plan (01:00) weźmie je przed resztą, aż każdy z nich zostanie sprawdzony.
        </p>

        {isPending && <EmptyState icon={LoaderCircle} title="Wczytuję powiaty…" className="[&_svg]:animate-spin" />}
        {isError && (
          <EmptyState icon={ServerCrash} title="Nie udało się pobrać powiatów" tone="bad">
            API nie odpowiada. Sprawdź, czy usługa „api” działa (docs/RUNBOOK.md).
          </EmptyState>
        )}

        {data && (
          <>
            <div className="relative mt-4">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
              <input
                type="search"
                value={rawQuery}
                onChange={(e) => setRawQuery(e.target.value)}
                aria-label="Szukaj powiatu"
                placeholder={`Szukaj powiatu lub województwa (min. ${MIN_QUERY} litery)`}
                className="h-10 w-full rounded-md border bg-surface pl-9 pr-3 text-sm placeholder:text-muted-foreground focus-visible:outline-2 focus-visible:outline-accent"
              />
            </div>
            {query !== null && (
              <p className="mt-2 text-xs text-muted-foreground" role="status" data-testid="search-summary">
                {visible.length === 0
                  ? `Brak wyników dla „${rawQuery.trim()}”`
                  : `Znaleziono ${visible.length} ${visible.length === 1 ? "powiat" : "powiatów"} w ${groups.length} ${groups.length === 1 ? "województwie" : "województwach"}`}
              </p>
            )}

            <div className="mt-4 flex flex-wrap items-center gap-2">
              <span className="text-sm" data-testid="checked-count">
                Sprawdzone: {checkedCount} z {items.length}
              </span>
              <div role="group" aria-label="Filtr powiatów" className="ml-auto flex gap-1">
                {FILTERS.map(({ key, label }) => (
                  <Button key={key} type="button" size="sm" variant={filter === key ? "default" : "outline"} aria-pressed={filter === key} onClick={() => setFilter(key)}>
                    {label}
                  </Button>
                ))}
              </div>
            </div>

            <div className="mt-4 grid gap-4 md:grid-cols-2">
            <Card className="p-3" aria-labelledby="queue-heading" role="region">
              <div className="flex flex-wrap items-center gap-2">
                <h2 id="queue-heading" className="flex items-center gap-2 text-sm font-semibold">
                  <ListOrdered className="size-4" aria-hidden /> Kolejka priorytetowa ({queue.length})
                </h2>
                {dirty && <Badge variant="warn">niezapisane zmiany</Badge>}
                <div className="ml-auto flex gap-2">
                  {dirty && (
                    <Button type="button" size="sm" variant="ghost" onClick={() => setStaged(null)} disabled={save.isPending}>
                      Cofnij
                    </Button>
                  )}
                  <Button type="button" size="sm" onClick={onSave} disabled={!dirty || save.isPending}>
                    {save.isPending ? "Zapisuję…" : dirty ? `Zapisz (${selected.size})` : "Zapisz"}
                  </Button>
                </div>
              </div>
              {queue.length === 0 ? (
                <p className="mt-2 text-sm text-muted-foreground">Brak. Nocny plan wybiera powiaty, które były przeszukane najdawniej.</p>
              ) : (
                <ol className="mt-2 flex flex-wrap gap-1.5" data-testid="priority-queue">
                  {queue.map((i) => (
                    <li key={i.teryt}>
                      <Badge variant="accent">{i.name}</Badge>
                    </li>
                  ))}
                </ol>
              )}
              {save.isError && (
                <p role="alert" className="mt-2 flex items-center gap-2 text-sm text-bad">
                  <CircleAlert className="size-4" aria-hidden /> Nie udało się zapisać listy. Wybór został zachowany, spróbuj ponownie.
                </p>
              )}
              {save.isSuccess && !dirty && (
                <p className="mt-2 flex items-center gap-2 text-sm text-ok">
                  <CircleCheck className="size-4" aria-hidden /> Zapisano. Lista zadziała od następnego nocnego planu.
                </p>
              )}
            </Card>

            <Card className="p-3" aria-labelledby="next-heading" role="region">
              <h2 id="next-heading" className="flex items-center gap-2 text-sm font-semibold">
                <Clock className="size-4" aria-hidden /> Następne w kolejce ({nextUp.length})
              </h2>
              <p className="mt-1 text-xs text-muted-foreground">
                Tyle powiatów plan nocny (01:00) przeszuka jako pierwsze, według zapisanej listy.
                {dirty && " Zaktualizuje się po zapisaniu zmian."}
              </p>
              {nextUp.length === 0 ? (
                <p className="mt-2 text-sm text-muted-foreground">Brak powiatów do przeszukania.</p>
              ) : (
                <ol className="mt-2 flex flex-col gap-1" data-testid="next-up">
                  {nextUp.map((i, n) => (
                    <li key={i.teryt} className="flex items-center gap-2 text-sm">
                      <span className="w-4 text-xs text-muted-foreground">{n + 1}.</span>
                      <span className="min-w-0 flex-1 truncate">{i.name}</span>
                      {i.priority && !i.checked ? <Badge variant="accent">priorytet</Badge> : <Badge>rotacja</Badge>}
                    </li>
                  ))}
                </ol>
              )}
            </Card>
            </div>

            <div className="mt-4 flex flex-col gap-2">
              {groups.map((group) => {
                const all = items.filter((i) => (i.voivodeship_teryt ?? "") === group.key);
                // While searching, every group with a result is open so the match is visible at once.
                const isOpen = query !== null || (opened[group.key] ?? all.some((i) => saved.has(i.teryt)));
                return (
                  <details
                    key={group.key}
                    open={isOpen}
                    onToggle={(e) => {
                      const open = e.currentTarget.open;
                      if (query === null) setOpened((o) => ({ ...o, [group.key]: open }));
                    }}
                    className="rounded-lg border bg-surface"
                    data-testid="voivodeship"
                  >
                    <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-3 py-2 text-sm font-semibold">
                      <span>{group.name}</span>
                      <span className={cn("text-xs font-normal text-muted-foreground")}>
                        sprawdzone {all.filter((i) => i.checked).length} z {all.length}
                      </span>
                    </summary>
                    <ul className="border-t">
                      {group.items.map((item) => (
                        <PowiatRow key={item.teryt} item={item} selected={selected.has(item.teryt)} onToggle={() => toggle(item.teryt)} />
                      ))}
                    </ul>
                  </details>
                );
              })}
              {visible.length === 0 && query === null && <EmptyState icon={CircleCheck} title="Brak powiatów w tym filtrze" />}
            </div>
          </>
        )}
      </div>
    </PageScroll>
  );
}
