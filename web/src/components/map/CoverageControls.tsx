import { useId } from "react";

import {
  COVERAGE_ITEM_LABELS,
  COVERAGE_ITEMS,
  COVERAGE_LEVEL_LABELS,
  type CoverageItem,
  type CoverageLevel,
} from "@/lib/coverage";

const selectClass = "h-9 w-full rounded-md border bg-surface px-2 text-sm";

interface Props {
  level: CoverageLevel;
  item: CoverageItem;
  onLevel: (level: CoverageLevel) => void;
  onItem: (item: CoverageItem) => void;
}

/** Choose the area level and the item whose coverage the map colours. Shown while the layer is on. */
export function CoverageControls({ level, item, onLevel, onItem }: Props) {
  const levelId = useId();
  const itemId = useId();
  return (
    <div className="mt-3 flex flex-col gap-2 border-t pt-3">
      <label htmlFor={levelId} className="flex flex-col gap-1 text-xs text-muted-foreground">
        Poziom obszaru
        <select id={levelId} className={selectClass} value={level} onChange={(e) => onLevel(e.target.value as CoverageLevel)}>
          {(Object.keys(COVERAGE_LEVEL_LABELS) as CoverageLevel[]).map((value) => (
            <option key={value} value={value}>
              {COVERAGE_LEVEL_LABELS[value]}
            </option>
          ))}
        </select>
      </label>
      <label htmlFor={itemId} className="flex flex-col gap-1 text-xs text-muted-foreground">
        Wskaźnik
        <select id={itemId} className={selectClass} value={item} onChange={(e) => onItem(e.target.value as CoverageItem)}>
          {COVERAGE_ITEMS.map((value) => (
            <option key={value} value={value}>
              {COVERAGE_ITEM_LABELS[value]}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
