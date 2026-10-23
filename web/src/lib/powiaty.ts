import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap, type PowiatItem } from "@/api/client";

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

export const MIN_QUERY = 3;

/** Lower case, no diacritics ("Łódzkie" -> "lodzkie"); `ł` has no decomposition, so it is mapped by hand. */
export function fold(text: string): string {
  return text
    .toLowerCase()
    .replaceAll("ł", "l")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "");
}

/** The query counts only from MIN_QUERY characters, so one or two letters never hide the list. */
export function activeQuery(raw: string): string | null {
  const q = fold(raw.trim());
  return q.length >= MIN_QUERY ? q : null;
}

/** A powiat matches on its name or code; a match on the województwo name keeps all of its powiats. */
export function matchesQuery(item: PowiatItem, query: string): boolean {
  return [item.name, item.teryt, item.voivodeship_name ?? ""].some((field) => fold(field).includes(query));
}
