import { Layers } from "lucide-react";

import { LAYER_GROUPS } from "./layers";

interface Props {
  visible: Record<string, boolean>;
  onToggle: (groupId: string, on: boolean) => void;
}

export function LayerControl({ visible, onToggle }: Props) {
  return (
    <fieldset className="absolute top-3 left-3 max-w-[16rem] rounded-md border bg-surface p-3 text-sm shadow-sm">
      <legend className="sr-only">Warstwy mapy</legend>
      <div className="mb-2 flex items-center gap-2 font-medium" aria-hidden>
        <Layers className="size-4" /> Warstwy
      </div>
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
          </li>
        ))}
      </ul>
    </fieldset>
  );
}
