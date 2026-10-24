import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { api, ApiError, unwrap, type CheckStep } from "@/api/client";

const TERMINAL = new Set(["done", "failed"]);
const POLL_MS = 2000;

/** A request the API refused, with the Polish reason it gave (422, 429, 503) instead of a bare status. */
export class CheckError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** FastAPI sends `detail` as a string, or as a list of validation problems. */
export function messageFromDetail(error: unknown, status: number): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return "Sprawdź wpisane dane: niektóre pola mają zły format.";
  return status >= 500 ? "Serwer nie odpowiada. Spróbuj za chwilę." : "Nie udało się wykonać polecenia.";
}

export function useResolve() {
  return useMutation({
    mutationFn: async (text: string) => {
      const { data, error, response } = await api.POST("/api/parcels/resolve", { body: { text } });
      if (data) return data;
      throw new CheckError(messageFromDetail(error, response.status), response.status);
    },
  });
}

export interface StartBody {
  parcels: string[];
  points?: { lat: number; lon: number }[];
}

export function useStartCheck() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: StartBody) => {
      const { data, error, response } = await api.POST("/api/checks", { body: { parcels: body.parcels, points: body.points ?? [] } });
      if (data) return data;
      throw new CheckError(messageFromDetail(error, response.status), response.status);
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ["checks"] }),
  });
}

/** One check, polled every 2 s until it is done or failed. */
export function useCheck(id: string) {
  return useQuery({
    queryKey: ["check", id],
    queryFn: () => unwrap(api.GET("/api/checks/{check_id}", { params: { path: { check_id: id } } })),
    refetchInterval: (query) => (query.state.data && TERMINAL.has(query.state.data.status) ? false : POLL_MS),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 3,
  });
}

export function useCheckSite(id: string, n: number, enabled: boolean) {
  return useQuery({
    queryKey: ["check", id, "site", n],
    queryFn: () => unwrap(api.GET("/api/checks/{check_id}/sites/{n}", { params: { path: { check_id: id, n } } })),
    enabled,
  });
}

export function useRecentChecks() {
  return useQuery({ queryKey: ["checks"], queryFn: () => unwrap(api.GET("/api/checks")), staleTime: 15_000 });
}

/** Steps done or failed over all steps: the bar never runs ahead of the work. */
export function progressOf(steps: CheckStep[]): { done: number; total: number } {
  return { done: steps.filter((s) => s.status === "done" || s.status === "failed").length, total: steps.length };
}

export function runningStep(steps: CheckStep[]): CheckStep | undefined {
  return steps.find((s) => s.status === "running");
}

/** The browser tab says where the check stands, so people can switch tabs while it runs. */
export function useTabTitle(title: string | null) {
  useEffect(() => {
    if (!title) return;
    const before = document.title;
    document.title = title;
    return () => {
      document.title = before;
    };
  }, [title]);
}

/** Layer names for the "Dane z" line of a result. */
export const LAYER_AGE_LABEL: Record<string, string> = {
  osm_power: "sieć (OSM)",
  prg: "granice (PRG)",
  isok: "strefy zalewowe",
  bdot10k: "budynki i lasy (BDOT10k)",
  midas: "obszary górnicze",
  ru: "plany (RU)",
};
