import type { components } from "@/api/schema";

import { formatDate, formatNumber } from "./format";

export type SitePrices = components["schemas"]["SitePrices"];
export type PriceGroup = components["schemas"]["PriceGroup"];

const NBSP = " ";

/** "6 zł/m²", "6,3 zł/m²", "138 zł/m²": one decimal below 100 zł, none above. */
export function formatZlM2(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "?";
  return `${formatNumber(value, Math.abs(value) < 100 ? 1 : 0)}${NBSP}zł/m²`;
}

/** "5 transakcji", "1 transakcja", "3 transakcje" (Polish plural rules). */
export function formatTransactions(n: number): string {
  const lastTwo = n % 100;
  const last = n % 10;
  const word = n === 1 ? "transakcja" : last >= 2 && last <= 4 && !(lastTwo >= 12 && lastTwo <= 14) ? "transakcje" : "transakcji";
  return `${n}${NBSP}${word}`;
}

/** "4–8 zł/m²" for the interquartile range, or null when the group has no transactions. */
export function formatRange(group: PriceGroup): string | null {
  if (group.p25_zl_m2 == null || group.p75_zl_m2 == null) return null;
  const digits = (v: number) => (Math.abs(v) < 100 ? 1 : 0);
  return `${formatNumber(group.p25_zl_m2, digits(group.p25_zl_m2))}–${formatNumber(group.p75_zl_m2, digits(group.p75_zl_m2))}${NBSP}zł/m²`;
}

/** "12.03.2025–03.10.2026", one date when both ends are the same day, or null without transactions. */
export function formatDateRange(group: PriceGroup): string | null {
  if (!group.first_date || !group.last_date) return null;
  const first = formatDate(group.first_date);
  const last = formatDate(group.last_date);
  return first === last ? first : `${first}–${last}`;
}

/** Parses what the user typed ("5,5", " 12.5 ") into a positive number, or null when it is not one. */
export function parseAskingPrice(raw: string): number | null {
  const value = Number(raw.trim().replace(",", "."));
  return raw.trim() !== "" && Number.isFinite(value) && value > 0 ? value : null;
}

export const WIDENED_NOTE = "Za mało transakcji w promieniu 10 km — dane z całego powiatu";
export const OUTLIER_WARNING = "Cena ofertowa ponad 10× wyższa od mediany dla gruntów rolnych";
