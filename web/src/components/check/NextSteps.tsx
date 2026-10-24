import { useState } from "react";

import type { CheckSiteDetail } from "@/api/client";
import { Button } from "@/components/ui/button";

/** The things no public service can do for us, as a checklist; the letters are ready to copy. */
export function NextSteps({ steps }: { steps: CheckSiteDetail["next_steps"] }) {
  const [copied, setCopied] = useState<string | null>(null);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const copy = async (key: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      /* the text is in the title attribute and the letter can be retyped; copying is a convenience */
    }
    setCopied(key);
    setTimeout(() => setCopied((c) => (c === key ? null : c)), 1600);
  };
  return (
    <ul className="m-0 list-none border-t p-0" data-testid="next-steps">
      {steps.map((step) => (
        <li key={step.key} className="grid grid-cols-[22px_minmax(0,1fr)_auto] items-start gap-2 border-b py-2.5 text-sm">
          <input
            type="checkbox"
            id={`step-${step.key}`}
            checked={checked[step.key] ?? false}
            onChange={(e) => setChecked((c) => ({ ...c, [step.key]: e.target.checked }))}
            className="mt-0.5 size-[18px] accent-[var(--accent)]"
          />
          <label htmlFor={`step-${step.key}`}>{step.title_pl}</label>
          {step.copy_text ? (
            <Button type="button" size="sm" variant="outline" onClick={() => copy(step.key, step.copy_text!)} title={step.copy_text}>
              {copied === step.key ? "Skopiowano" : "Kopiuj treść"}
            </Button>
          ) : (
            <span />
          )}
        </li>
      ))}
    </ul>
  );
}
