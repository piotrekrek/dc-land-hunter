import { Check, CircleHelp, X } from "lucide-react";

import type { MarkKind } from "@/lib/sites";
import { cn } from "@/lib/utils";

/** A mark is a shape and a word, never colour alone: ✓ checked, ≈ estimate, ? no data, ✕ condition not met. */
const MARKS: Record<MarkKind, { label: string; tone: string }> = {
  ok: { label: "sprawdzone w źródle", tone: "text-ok" },
  est: { label: "szacunek", tone: "text-accent" },
  unk: { label: "brak danych", tone: "text-muted-foreground" },
  bad: { label: "nie spełnia warunku", tone: "text-bad" },
};

export function Mark({ kind, className }: { kind: MarkKind; className?: string }) {
  const { label, tone } = MARKS[kind];
  return (
    <span role="img" aria-label={label} className={cn("inline-flex size-5 items-center justify-center", tone, className)}>
      {kind === "ok" && <Check className="size-4" strokeWidth={2.5} aria-hidden />}
      {kind === "est" && (
        <span aria-hidden className="text-base font-semibold leading-none">
          ≈
        </span>
      )}
      {kind === "unk" && <CircleHelp className="size-4" aria-hidden />}
      {kind === "bad" && <X className="size-4" strokeWidth={2.5} aria-hidden />}
    </span>
  );
}

export function MarkLegend() {
  return (
    <p className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground" aria-label="Znaczenie znaków">
      {(Object.keys(MARKS) as MarkKind[]).map((kind) => (
        <span key={kind} className="inline-flex items-center gap-1.5">
          <Mark kind={kind} />
          {MARKS[kind].label}
        </span>
      ))}
    </p>
  );
}
