import { useQuery } from "@tanstack/react-query";

import { api, unwrap } from "@/api/client";
import { cn } from "@/lib/utils";

export function ApiStatus() {
  const { data, isError, isPending } = useQuery({
    queryKey: ["health"],
    queryFn: () => unwrap(api.GET("/api/health")),
    refetchInterval: 30_000,
    retry: 1,
  });

  const state = isPending ? "pending" : isError ? "down" : data.status === "ok" ? "ok" : "degraded";
  const label = {
    pending: "Sprawdzam połączenie…",
    ok: "Połączono z API",
    degraded: "Baza danych niedostępna",
    down: "API niedostępne",
  }[state];

  return (
    <span className="flex items-center gap-2 text-xs text-muted-foreground" title={label} data-testid="api-status">
      <span
        aria-hidden
        className={cn(
          "size-2 rounded-full",
          state === "ok" && "bg-ok",
          state === "degraded" && "bg-warn",
          state === "down" && "bg-bad",
          state === "pending" && "animate-pulse bg-muted-foreground",
        )}
      />
      <span className="sr-only xl:not-sr-only">{label}</span>
    </span>
  );
}
