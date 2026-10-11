import { formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

/** A 0–100 score as a round badge: green from 70, amber from 40, neutral below. */
export function ScoreBadge({ score, excluded, className }: { score: number; excluded?: boolean; className?: string }) {
  const tone = excluded ? "bg-surface-2 text-muted-foreground" : score >= 70 ? "bg-ok-soft text-ok" : score >= 40 ? "bg-warn-soft text-warn" : "bg-surface-2 text-foreground";
  return (
    <span
      className={cn("flex size-12 shrink-0 flex-col items-center justify-center rounded-full text-sm font-semibold", tone, className)}
      aria-label={`Wynik ${formatNumber(score, 0)} na 100`}
    >
      {formatNumber(score, 0)}
      <span className="text-[10px] font-normal opacity-80">/100</span>
    </span>
  );
}
