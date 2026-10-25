import { CircleAlert, CircleCheck, ListOrdered, LoaderCircle, Search, ServerCrash, X } from "lucide-react";
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

/** One state per queue row. Outside the AOI wins: the nightly plan skips such a powiat, whatever else is true. */
function QueueState({ item, nextUp, staged }: { item: PowiatItem; nextUp: boolean; staged: boolean }) {
  if (!item.in_aoi) return <Badge variant="warn" title="Poza obszarem poszukiwań: nocny plan go pomija">poza obszarem</Badge>;
  if (nextUp) return <Badge variant="accent">{item.priority && !item.checked ? "następny o 01:00 · priorytet" : "następny o 01:00"}</Badge>;
  if (item.checked) return <Badge variant="ok">sprawdzony</Badge>;
  return <Badge>{staged ? "priorytet · czeka" : "czeka"}</Badge>;
}

function StatusBadge({ item }: { item: PowiatItem }) {
  if (!item.in_aoi) return <Badge variant="warn">Poza obszarem</Badge>;
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
      {item.checked || !item.in_aoi ? (
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
  const [showOutside, setShowOutside] = useState(false);
  const query = activeQuery(rawQuery);

  const all = data?.items ?? [];
  // Powiats outside the AOI are never searched, so they are out of the counts and lists unless asked for.
  const items = showOutside ? all : all.filter((i) => i.in_aoi);
  const outsideCount = all.length - all.filter((i) => i.in_aoi).length;
  const saved = new Set(all.filter((i) => i.priority).map((i) => i.teryt));
  const selected = staged ?? saved;
  const dirty = staged !== null && !sameSet(staged, saved);
  const checkedCount = items.filter((i) => i.checked).length;
  const visible = items.filter(
    (i) => (filter === "all" || (filter === "checked") === i.checked) && (query === null || matchesQuery(i, query)),
  );
  // One queue: what the next night takes first, then the other priority powiats. A priority powiat outside the AOI
  // stays in it, marked, so the owner sees why it will not run. A removal staged but not yet saved leaves at once.
  const byCode = new Map(all.map((i) => [i.teryt, i]));
  const nextUpSet = new Set(data?.next_up ?? []);
  const queueCodes = [...new Set([...(data?.next_up ?? []), ...all.filter((i) => selected.has(i.teryt)).map((i) => i.teryt)])];
  const queue = queueCodes.flatMap((t) => byCode.get(t) ?? []).filter((i) => selected.has(i.teryt) || !i.priority);
  const groups = groupByVoivodeship(visible);

  const toggle = (teryt: string) => {
    const next = new Set(selected);
    if (!next.delete(teryt)) next.add(teryt);
    setStaged(sameSet(next, saved) ? null : next);
  };

  const onSave = () => save.mutate([...selected], { onSuccess: () => setStaged(null) });

  return (
    <PageScroll label="Kolejka">
      <div className="mx-auto max-w-5xl p-4 md:p-6">
        <h1 className="text-xl font-semibold">Kolejka</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Powiat jest „sprawdzony”, gdy ma udane wyszukiwanie. Zaznacz „Priorytet” przy powiatach, które mają być przeszukane
          najpierw, i zapisz listę. Nocny plan (01:00) weźmie je przed resztą, aż każdy z nich zostanie sprawdzony. Lista
          obejmuje tylko obszar poszukiwań (lewy brzeg Wisły); powiatów poza nim plan nie przeszukuje.
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
              {outsideCount > 0 && (
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <input type="checkbox" checked={showOutside} onChange={(e) => setShowOutside(e.target.checked)} />
                  pokaż poza obszarem ({outsideCount})
                </label>
              )}
              <div role="group" aria-label="Filtr powiatów" className="ml-auto flex gap-1">
                {FILTERS.map(({ key, label }) => (
                  <Button key={key} type="button" size="sm" variant={filter === key ? "default" : "outline"} aria-pressed={filter === key} onClick={() => setFilter(key)}>
                    {label}
                  </Button>
                ))}
              </div>
            </div>

            <Card className="mt-4 p-3" aria-labelledby="queue-heading" role="region">
              <div className="flex flex-wrap items-center gap-2">
                <h2 id="queue-heading" className="flex items-center gap-2 text-sm font-semibold">
                  <ListOrdered className="size-4" aria-hidden /> Kolejka ({queue.length})
                </h2>
                <div className="ml-auto flex items-center gap-2">
                  {dirty && <span className="text-xs font-medium text-warn">Niezapisane zmiany</span>}
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
              <p className="mt-1 text-xs text-muted-foreground">
                Nocny plan (01:00) przeszuka je w tej kolejności: najpierw priorytety, potem powiaty przeszukane najdawniej.
                {dirty && " Kolejność zaktualizuje się po zapisaniu zmian."}
              </p>
              {queue.length === 0 ? (
                <p className="mt-2 text-sm text-muted-foreground">Brak powiatów do przeszukania.</p>
              ) : (
                <ol className="mt-2 flex flex-col gap-1" data-testid="queue">
                  {queue.map((i, n) => (
                    <li key={i.teryt} className="flex items-center gap-2 text-sm" data-testid="queue-row">
                      <span className="w-5 text-xs text-muted-foreground">{n + 1}.</span>
                      <span className="min-w-0 flex-1 truncate">{i.name}</span>
                      <QueueState item={i} nextUp={nextUpSet.has(i.teryt)} staged={selected.has(i.teryt)} />
                      {selected.has(i.teryt) && (
                        <button
                          type="button"
                          aria-label={`Usuń z kolejki: ${i.name}`}
                          title="Usuń z kolejki"
                          disabled={save.isPending}
                          onClick={() => toggle(i.teryt)}
                          className="rounded-sm p-0.5 hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50 [&_svg]:size-3.5"
                        >
                          <X aria-hidden />
                        </button>
                      )}
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

            <div className="mt-4 flex flex-col gap-2">
              {groups.map((group) => {
                const inGroup = items.filter((i) => (i.voivodeship_teryt ?? "") === group.key);
                // While searching, every group with a result is open so the match is visible at once.
                const isOpen = query !== null || (opened[group.key] ?? inGroup.some((i) => saved.has(i.teryt)));
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
                        sprawdzone {inGroup.filter((i) => i.checked).length} z {inGroup.length}
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
