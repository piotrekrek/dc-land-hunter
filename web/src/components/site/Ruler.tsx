import { formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The score thresholds from config/scoring.yaml: below 40 weak, from 70 strong. */
export const RULER_TICKS = [40, 70] as const;

const clamp = (value: number) => Math.min(100, Math.max(0, value));

/**
 * The score on a 0–100 ruler instead of a round badge. The solid part runs to the score, the hatched part to the
 * score the site could reach once the missing data is clarified: the two numbers a reader needs, in one glance.
 */
export function Ruler({ score, upside, dim = false, className }: { score: number; upside: number; dim?: boolean; className?: string }) {
  const now = clamp(score);
  const potential = Math.max(now, clamp(upside));
  const label = `Linijka wyniku: ${formatNumber(now, 0)}, potencjał ${formatNumber(potential, 0)}, progi ${RULER_TICKS.join(" i ")}`;
  return (
    <div className={className} data-testid="ruler">
      <div className="flex items-baseline gap-2.5">
        <span className="text-4xl font-medium leading-none tabular-nums tracking-tight" aria-label={`Wynik ${formatNumber(now, 0)} na 100`}>
          {formatNumber(now, 0)}
          <small className="ml-0.5 text-sm font-normal text-muted-foreground">/100</small>
        </span>
        <span className="text-sm text-muted-foreground">
          do <b className="font-semibold text-foreground">{formatNumber(potential, 0)}</b> pkt po wyjaśnieniu danych
        </span>
      </div>
      <div role="img" aria-label={label} className={cn("relative mt-4 mb-9", dim && "opacity-55")}>
        <div className="relative h-3 border border-ink bg-paper">
          <div className="absolute inset-y-0 left-0 bg-ink" style={{ width: `${now}%` }} data-testid="ruler-solid" />
          <div
            className="absolute inset-y-0"
            style={{
              left: `${now}%`,
              width: `${potential - now}%`,
              backgroundImage: "repeating-linear-gradient(135deg, var(--ink) 0 1.5px, transparent 1.5px 5px)",
            }}
            data-testid="ruler-hatch"
          />
          {RULER_TICKS.map((tick) => (
            <span key={tick} className="absolute -top-[5px] h-[22px] border-l border-ink" style={{ left: `${tick}%` }}>
              <span className="absolute top-6 -translate-x-1/2 font-cond text-[11px] text-ink-2">{tick}</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
