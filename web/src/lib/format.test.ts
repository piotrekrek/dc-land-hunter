import { describe, expect, it } from "vitest";

import { formatDate, formatDateTime, formatHa, formatKm, formatMW, formatNumber, formatPercent, formatPln } from "./format";

const sp = (s: string) => s.replace(/\u00a0/g, " ");

describe("Polish formatting", () => {
  it("formats the brief's examples", () => {
    expect(sp(formatHa(12.5))).toBe("12,5 ha");
    expect(sp(formatMW(8))).toBe("8 MW");
    expect(sp(formatKm(2.345))).toBe("2,3 km");
  });

  it("keeps value and unit together with a non-breaking space", () => {
    expect(formatHa(10)).toBe("10\u00a0ha");
  });

  it("groups thousands the Polish way", () => {
    expect(sp(formatNumber(1234567))).toBe("1 234 567");
    expect(sp(formatPln(3600000))).toBe("3 600 000 zł");
  });

  it("shows missing values as '?' (never a guess)", () => {
    expect(formatHa(null)).toBe("?");
    expect(formatMW(undefined)).toBe("?");
    expect(formatPercent(Number.NaN)).toBe("?");
  });

  it("formats percentages", () => {
    expect(formatPercent(0.456)).toBe("46%");
  });

  it("formats dates as dd.mm.rrrr in Europe/Warsaw", () => {
    expect(formatDate("2026-10-05T23:30:00Z")).toBe("06.10.2026"); // after midnight in Warsaw
    expect(formatDate(null)).toBe("—");
    expect(formatDateTime("2026-10-10T16:05:00Z")).toBe("10.10.2026, 18:05");
  });
});
