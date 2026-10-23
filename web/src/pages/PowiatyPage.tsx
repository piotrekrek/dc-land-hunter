import { CircleAlert, CircleCheck, ListOrdered, LoaderCircle, ServerCrash } from "lucide-react";
import { useState } from "react";

import type { PowiatItem } from "@/api/client";
import { EmptyState } from "@/components/EmptyState";
import { PageScroll } from "@/components/PageScroll";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import { usePowiaty, useSavePriority } from "@/lib/powiaty";
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

  const items = data?.items ?? [];
  const saved = new Set(items.filter((i) => i.priority).map((i) => i.teryt));
  const selected = staged ?? saved;
  const dirty = staged !== null && !sameSet(staged, saved);
  const checkedCount = items.filter((i) => i.checked).length;
  const queue = items.filter((i) => selected.has(i.teryt));
  const visible = items.filter((i) => filter === "all" || (filter === "checked") === i.checked);

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

            <Card className="mt-4 p-3" aria-labelledby="queue-heading" role="region">
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

            <div className="mt-4 flex flex-col gap-2">
              {groupByVoivodeship(visible).map((group) => {
                const all = items.filter((i) => (i.voivodeship_teryt ?? "") === group.key);
                const isOpen = opened[group.key] ?? all.some((i) => saved.has(i.teryt));
                return (
                  <details
                    key={group.key}
                    open={isOpen}
                    onToggle={(e) => {
                      const open = e.currentTarget.open;
                      setOpened((o) => ({ ...o, [group.key]: open }));
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
              {visible.length === 0 && <EmptyState icon={CircleCheck} title="Brak powiatów w tym filtrze" />}
            </div>
          </>
        )}
      </div>
    </PageScroll>
  );
}
