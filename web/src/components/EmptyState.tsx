import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface Props {
  icon: LucideIcon;
  title: string;
  children?: ReactNode;
  tone?: "neutral" | "bad";
  className?: string;
}

export function EmptyState({ icon: Icon, title, children, tone = "neutral", className }: Props) {
  return (
    <div className={cn("flex flex-col items-center gap-3 px-6 py-12 text-center", className)} role="status">
      <span
        className={cn(
          "flex size-11 items-center justify-center rounded-full",
          tone === "bad" ? "bg-bad-soft text-bad" : "bg-surface-2 text-muted-foreground",
        )}
      >
        <Icon className="size-5" aria-hidden />
      </span>
      <p className="font-medium">{title}</p>
      {children && <div className="max-w-sm text-sm text-muted-foreground">{children}</div>}
    </div>
  );
}
