import { describe, expect, it } from "vitest";

import { formatDateRange, formatRange, formatTransactions, formatZlM2, parseAskingPrice, type PriceGroup } from "./prices";

const sp = (s: string) => s.replace(/\u00a0/g, " ");

const group = (over: Partial<PriceGroup> = {}): PriceGroup => ({
  count: 5,
  median_zl_m2: 6,
  p25_zl_m2: 4,
  p75_zl_m2: 8.5,
  first_date: "2025-03-12",
  last_date: "2026-10-03",
  widened: false,
  confidence: "verified",
  ...over,
});

describe("price formatting", () => {
  it("formats zł/m² with comma decimals and no trailing zero", () => {
    expect(sp(formatZlM2(6))).toBe("6 zł/m²");
    expect(sp(formatZlM2(6.25))).toBe("6,3 zł/m²");
    expect(sp(formatZlM2(138.49))).toBe("138 zł/m²");
    expect(formatZlM2(null)).toBe("?");
  });

  it("shows the interquartile range", () => {
    expect(sp(formatRange(group())!)).toBe("4–8,5 zł/m²");
    expect(formatRange(group({ p25_zl_m2: null, p75_zl_m2: null }))).toBeNull();
  });

  it("uses Polish plural forms for transactions", () => {
    expect([1, 2, 5, 12, 22, 25].map((n) => sp(formatTransactions(n)))).toEqual([
      "1 transakcja",
      "2 transakcje",
      "5 transakcji",
      "12 transakcji",
      "22 transakcje",
      "25 transakcji",
    ]);
  });

  it("writes the date range as dd.mm.rrrr", () => {
    expect(formatDateRange(group())).toBe("12.03.2025–03.10.2026");
    expect(formatDateRange(group({ last_date: "2025-03-12" }))).toBe("12.03.2025");
    expect(formatDateRange(group({ first_date: null, last_date: null }))).toBeNull();
  });
});

describe("parseAskingPrice", () => {
  it("accepts a comma or a dot and rejects zero, text and empty input", () => {
    expect(parseAskingPrice("12,5")).toBe(12.5);
    expect(parseAskingPrice(" 40.25 ")).toBe(40.25);
    expect(parseAskingPrice("0")).toBeNull();
    expect(parseAskingPrice("-3")).toBeNull();
    expect(parseAskingPrice("abc")).toBeNull();
    expect(parseAskingPrice("")).toBeNull();
  });
});
