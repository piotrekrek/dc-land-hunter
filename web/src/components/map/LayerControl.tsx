import { Layers } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { LAYER_GROUPS } from "./layers";

interface Props {
  visible: Record<string, boolean>;
  onToggle: (groupId: string, on: boolean) => void;
  /** Controls of one layer (e.g. the coverage selectors), shown below the list of layers. */
  children?: ReactNode;
}

/** Open on a desktop (Tailwind's lg breakpoint, 1024px), collapsed on a phone where the panel covers the map. */
const initiallyOpen = (): boolean =>
  typeof matchMedia === "function" ? matchMedia("(min-width: 1024px)").matches : true;

export function LayerControl({ visible, onToggle, children }: Props) {
  const [open, setOpen] = useState(initiallyOpen);
  const panelId = useId();
  return (
    <div className="absolute top-3 left-3 flex flex-col items-start gap-2 text-sm">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 rounded-md border bg-surface px-3 py-2 font-medium shadow-sm focus-visible:outline-2 focus-visible:outline-accent"
      >
        <Layers className="size-4" aria-hidden /> Warstwy mapy
      </button>
      {open && (
        <fieldset id={panelId} className="max-w-[16rem] rounded-md border bg-surface p-3 text-sm shadow-sm">
          <legend className="sr-only">Warstwy mapy</legend>
          <ul className="flex flex-col gap-1.5">
            {LAYER_GROUPS.map((g) => (
              <li key={g.id}>
                <label className="flex cursor-pointer items-center gap-2">
                  <input
                    type="checkbox"
                    className="size-4 accent-[var(--accent)]"
                    checked={visible[g.id] ?? g.defaultOn}
                    onChange={(e) => onToggle(g.id, e.target.checked)}
                  />
                  {g.color && <span aria-hidden className="h-0.5 w-5 rounded" style={{ background: g.color }} />}
                  {g.label}
                </label>
                {g.legend && (
                  <ul className="mt-1 ml-6 flex flex-col gap-0.5 text-xs text-muted-foreground">
                    {g.legend.map((item) => (
                      <li key={item.label} className="flex items-center gap-2">
                        <span aria-hidden className="size-2.5 rounded-sm" style={{ background: item.color }} />
                        {item.label}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
          {children}
        </fieldset>
      )}
    </div>
  );
}
