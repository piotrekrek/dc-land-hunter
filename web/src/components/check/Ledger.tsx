import { Check, LoaderCircle, X } from "lucide-react";

import type { CheckStep } from "@/api/client";
import { formatDuration } from "@/lib/format";
import { progressOf } from "@/lib/check";
import { cn } from "@/lib/utils";

function StepMark({ status }: { status: CheckStep["status"] }) {
  if (status === "done") return <Check className="size-4 text-ok" strokeWidth={2.5} aria-label="gotowe" role="img" />;
  if (status === "failed") return <X className="size-4 text-bad" strokeWidth={2.5} aria-label="błąd" role="img" />;
  if (status === "running") {
    return <LoaderCircle className="size-4 animate-spin text-accent motion-reduce:animate-none" aria-label="w toku" role="img" />;
  }
  return <span className="size-1.5 rounded-full bg-ink-3" aria-label="czeka" role="img" />;
}

/** The progress bar: steps done over all steps, never a made-up animation. `aria-valuetext` says it in words. */
export function ProgressBar({ steps, estimateSeconds }: { steps: CheckStep[]; estimateSeconds?: number | null }) {
  const { done, total } = progressOf(steps);
  const eta = estimateSeconds != null ? `, ok. ${formatDuration(estimateSeconds)}` : "";
  return (
    <div>
      <div
        role="progressbar"
        aria-label="Postęp sprawdzenia"
        aria-valuemin={0}
        aria-valuemax={Math.max(total, 1)}
        aria-valuenow={done}
        aria-valuetext={`${done} z ${total} kroków${eta}`}
        className="h-2.5 border border-ink bg-paper"
      >
        <div className="h-full bg-ink transition-[width] motion-reduce:transition-none" style={{ width: `${total ? (done / total) * 100 : 0}%` }} />
      </div>
      <p className="mt-1.5 text-sm text-muted-foreground tabular-nums">
        {done} z {total} kroków{estimateSeconds != null && <> · ok. {formatDuration(estimateSeconds)}</>}
      </p>
    </div>
  );
}

/** What each step found, as it finishes: the investigation happening in front of the reader. */
export function Ledger({ steps, className }: { steps: CheckStep[]; className?: string }) {
  return (
    <ol className={cn("border-t border-ink", className)} data-testid="ledger">
      {steps.map((step) => (
        <li key={`${step.parcel ?? "all"}-${step.key}`} className="grid grid-cols-[20px_minmax(0,1fr)_auto] gap-x-3 border-b py-2 text-sm" data-status={step.status}>
          <span className="mt-0.5">
            <StepMark status={step.status} />
          </span>
          <span className="min-w-0">
            <span className={cn("font-medium", step.status === "waiting" && "text-muted-foreground")}>{step.label_pl}</span>
            {step.finding_pl && <span className="block text-muted-foreground">{step.finding_pl}</span>}
            {step.error && (
              <span className="block break-words text-bad">
                Nie udało się: {step.error}. Ten warunek zostanie wpisany jako „nie sprawdzono”.
              </span>
            )}
          </span>
          <span className="text-xs text-muted-foreground tabular-nums">{step.seconds != null && step.status !== "waiting" ? `${step.seconds.toFixed(1).replace(".", ",")} s` : ""}</span>
        </li>
      ))}
    </ol>
  );
}
