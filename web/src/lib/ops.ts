import { useQuery } from "@tanstack/react-query";

import { api, unwrap } from "@/api/client";

const REFRESH_MS = 5 * 60_000;

/** Job health for admins. Shared by the Zadania page and the nav badge, so one request serves both. */
export function useOpsJobs(enabled: boolean) {
  return useQuery({
    queryKey: ["ops-jobs"],
    queryFn: () => unwrap(api.GET("/api/ops/jobs")),
    enabled,
    refetchInterval: REFRESH_MS,
  });
}
