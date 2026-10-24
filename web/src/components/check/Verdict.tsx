import { Check, CircleHelp, X } from "lucide-react";

import type { Verdict as VerdictData } from "@/api/client";
import { cn } from "@/lib/utils";

const KIND = {
  odpada: { icon: X, tone: "text-bad border-bad", label: "odpada" },
  wymaga_wyjasnien: { icon: CircleHelp, tone: "text-warn border-warn", label: "wymaga wyjaśnień" },
  warto: { icon: Check, tone: "text-ok border-ok", label: "warto sprawdzać dalej" },
} as const;

/** The verdict is a sentence with the deciding reason first; the shape and the words, not only colour, carry the state. */
export function Verdict({ verdict, className }: { verdict: VerdictData; className?: string }) {
  const { icon: Icon, tone, label } = KIND[verdict.kind];
  return (
    <div className={cn("flex items-start gap-3", className)} data-testid="verdict" data-kind={verdict.kind}>
      <span className={cn("mt-1 flex size-9 shrink-0 items-center justify-center rounded-full border-2", tone)} role="img" aria-label={label}>
        <Icon className="size-5" strokeWidth={2.5} aria-hidden />
      </span>
      <p className="m-0 text-2xl leading-snug font-medium text-balance">{verdict.text_pl}</p>
    </div>
  );
}
