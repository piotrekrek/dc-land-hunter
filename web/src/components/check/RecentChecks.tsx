import { Link } from "react-router";

import type { CheckListItem } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { formatDate } from "@/lib/format";

const STATE: Record<string, { label: string; variant: "neutral" | "accent" | "bad" }> = {
  pending: { label: "w kolejce", variant: "neutral" },
  running: { label: "w trakcie", variant: "accent" },
  failed: { label: "błąd", variant: "bad" },
};

/** Earlier checks, newest first, for everyone on the team. */
export function RecentChecks({ items }: { items: CheckListItem[] }) {
  if (items.length === 0) {
    return <p className="text-sm text-muted-foreground">Jeszcze nic nie sprawdzano. Wklej numer działki od pośrednika.</p>;
  }
  return (
    <ul className="m-0 list-none border-t p-0" data-testid="recent-checks">
      {items.map((item) => {
        const state = STATE[item.status];
        return (
          <li key={item.id} className="border-b">
            <Link to={`/sprawdz-dzialke/${item.id}`} className="grid gap-x-4 gap-y-0.5 py-2.5 text-sm hover:bg-surface-2 sm:grid-cols-[minmax(0,2fr)_minmax(0,3fr)_auto]">
              <span className="font-cond font-medium break-all">{item.parcels.join(", ")}</span>
              <span className="text-muted-foreground">
                {state ? <Badge variant={state.variant}>{state.label}</Badge> : (item.verdict?.text_pl ?? "")}
              </span>
              <span className="text-xs text-muted-foreground tabular-nums sm:text-right">
                {formatDate(item.created_at)} · {item.requested_by}
              </span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
