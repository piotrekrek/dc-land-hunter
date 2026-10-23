import { describe, expect, it } from "vitest";

import type { PowiatItem } from "@/api/client";

import { activeQuery, fold, matchesQuery } from "./powiaty";

const item = (over: Partial<PowiatItem>): PowiatItem => ({
  teryt: "3011",
  name: "powiat koniński",
  voivodeship_teryt: "30",
  voivodeship_name: "wielkopolskie",
  checked: false,
  last_ok_at: null,
  last_attempt_at: null,
  status: null,
  sites: null,
  error: null,
  priority: false,
  ...over,
});

describe("fold", () => {
  it("lower-cases and drops Polish diacritics, including ł", () => {
    expect(fold("Łódzkie")).toBe("lodzkie");
    expect(fold("Świętokrzyskie")).toBe("swietokrzyskie");
    expect(fold("Żuromin")).toBe("zuromin");
  });
});

describe("activeQuery", () => {
  it("needs three characters after trimming", () => {
    expect(activeQuery("ko")).toBeNull();
    expect(activeQuery("  ko ")).toBeNull();
    expect(activeQuery("kon")).toBe("kon");
    expect(activeQuery(" Łód ")).toBe("lod");
  });
});

describe("matchesQuery", () => {
  it("matches the name, the code and the voivodeship name", () => {
    expect(matchesQuery(item({}), "konin")).toBe(true);
    expect(matchesQuery(item({}), "3011")).toBe(true);
    expect(matchesQuery(item({}), "wielkopol")).toBe(true);
    expect(matchesQuery(item({}), "poznan")).toBe(false);
  });

  it("copes with a powiat that has no voivodeship name", () => {
    expect(matchesQuery(item({ voivodeship_name: null }), "wielkopol")).toBe(false);
  });
});
