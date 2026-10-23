import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/api/client";

const KEY = ["powiaty"];

/** Every powiat with its search status and whether it is on the priority list. */
export function usePowiaty() {
  return useQuery({ queryKey: KEY, queryFn: () => unwrap(api.GET("/api/powiats")) });
}

/** Replaces the whole priority list; the nightly planner reads it at 01:00. */
export function useSavePriority() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (teryts: string[]) => unwrap(api.PUT("/api/powiats/priority", { body: { teryts } })),
    onSuccess: (list) => client.setQueryData(KEY, list),
  });
}
