import { formatDate, formatNumber } from "@/lib/format";
import { CATEGORY_LABEL, CONFIDENCE_LABEL, NOT_YET_KEYS, markForConfidence, type SiteDetail } from "@/lib/sites";

import { Mark, MarkLegend } from "./Mark";

const CATEGORY_ORDER = ["grid", "zoning", "land", "connectivity", "bonus", "penalty"];

type ProtokolSite = Pick<SiteDetail, "items" | "categories" | "exclusions">;

function Group({ title, summary, testId, children }: { title: string; summary?: string; testId?: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-ink first:border-t-0" data-testid={testId}>
      <div className="flex flex-wrap items-baseline justify-between gap-2 pt-3.5 pb-1.5">
        <h3 className="text-sm font-semibold">{title}</h3>
        {summary && <span className="text-sm text-muted-foreground tabular-nums">{summary}</span>}
      </div>
      <ul>{children}</ul>
    </section>
  );
}

function Row({ mark, label, finding, source, points }: { mark: React.ReactNode; label: string; finding: string; source?: React.ReactNode; points?: string }) {
  return (
    <li className="grid grid-cols-[22px_minmax(0,1fr)_auto] gap-x-3 gap-y-1 border-t py-2.5 text-sm sm:grid-cols-[24px_minmax(150px,1.1fr)_minmax(0,3fr)_72px] sm:items-baseline">
      <span className="mt-0.5 self-start">{mark}</span>
      <span className="font-medium">{label}</span>
      <span className="col-span-2 col-start-2 row-start-2 min-w-0 sm:col-span-1 sm:col-start-auto sm:row-start-auto">
        {finding}
        {source && <span className="mt-0.5 block text-xs text-muted-foreground">{source}</span>}
      </span>
      <span className="col-start-3 row-start-1 text-right tabular-nums whitespace-nowrap sm:col-start-auto sm:row-start-auto">{points}</span>
    </li>
  );
}

/**
 * The inspection record: a ruled table, one row per criterion with the finding in plain Polish, a mark, and the
 * source and date in a quieter second line. Replaces the stack of equal cards (owner verdict, 24.10.2026).
 */
export function Protokol({ site }: { site: ProtokolSite }) {
  // Criteria nothing feeds yet are folded into one line below the table: they always read "brak danych, 0 pkt".
  const noData = site.items.filter((i) => NOT_YET_KEYS.has(i.key) && i.confidence === "missing");
  const shown = site.items.filter((i) => !noData.includes(i));
  const groups = CATEGORY_ORDER.map((cat) => ({
    cat,
    items: shown.filter((i) => i.category === cat),
    summary: site.categories.find((c) => c.category === cat),
  })).filter((g) => g.items.length > 0);
  const noDataMax = noData.reduce((sum, i) => sum + i.max_points, 0);

  return (
    <div data-testid="protokol">
      <div className="mb-2">
        <MarkLegend />
      </div>
      <div className="border-t-2 border-ink">
        {site.exclusions.length > 0 && (
          <Group title="Warunki konieczne" summary={`niespełnione: ${site.exclusions.length}`} testId="gates-group">
            {site.exclusions.map((e) => (
              <Row
                key={e.key}
                mark={<Mark kind="bad" />}
                label={e.mode === "exclude" ? "Wyklucza lokalizację" : "Kara punktowa"}
                finding={e.reason_pl}
                points={e.penalty_points > 0 ? `−${formatNumber(e.penalty_points, 0)} pkt` : undefined}
              />
            ))}
          </Group>
        )}
        {groups.map(({ cat, items, summary }) => (
          <Group
            key={cat}
            title={CATEGORY_LABEL[cat] ?? cat}
            summary={summary ? `${formatNumber(summary.points, 0)} z ${formatNumber(summary.max_points, 0)} pkt` : undefined}
            testId="breakdown-group"
          >
            {items.map((item) => (
              <Row
                key={item.key}
                mark={<Mark kind={markForConfidence(item.confidence)} />}
                label={item.label_pl}
                finding={item.explanation_pl}
                source={
                  <>
                    <span>{CONFIDENCE_LABEL[item.confidence]}</span>
                    {item.source_id && (
                      <span>
                        {" · "}źródło: {item.source_id}
                        {item.as_of ? `, stan na ${formatDate(item.as_of)}` : ""}
                      </span>
                    )}
                  </>
                }
                points={`${item.points >= 0 ? "+" : ""}${formatNumber(item.points, 1)} / ${formatNumber(item.max_points, 1)}`}
              />
            ))}
          </Group>
        ))}
      </div>
      {noData.length > 0 && (
        <details className="mt-3 border-y py-2 text-sm" data-testid="no-data-group">
          <summary className="cursor-pointer">
            <span className="font-medium">Kryteria bez danych w tej wersji ({noData.length})</span>{" "}
            <span className="text-muted-foreground">
              do {formatNumber(noDataMax, 1)} pkt, liczone jako 0
            </span>
          </summary>
          <ul className="m-0 mt-2 list-none p-0">
            {noData.map((item) => (
              <li key={item.key} className="grid grid-cols-[22px_minmax(0,1fr)_auto] gap-x-3 border-t py-2">
                <Mark kind="unk" />
                <span>
                  <span className="font-medium">{item.label_pl}</span>
                  <span className="block text-xs text-muted-foreground">{item.explanation_pl}</span>
                </span>
                <span className="text-right whitespace-nowrap tabular-nums">0 / {formatNumber(item.max_points, 1)}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
