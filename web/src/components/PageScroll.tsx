import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Scrollable page body. It is focusable so keyboard users can scroll it even when it contains no links
 * (axe: scrollable-region-focusable).
 */
export function PageScroll({ label, className, children }: { label: string; className?: string; children: ReactNode }) {
  return (
    <div tabIndex={0} role="region" aria-label={label} className={cn("h-full overflow-y-auto focus-visible:outline-offset-[-2px]", className)}>
      {children}
    </div>
  );
}
