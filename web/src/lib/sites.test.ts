import { describe, expect, it } from "vitest";

import { DEFAULT_FILTERS, describeFilters, isDefaultFilters, parseFilters, placeLine, toParams, uniqueBySiteId } from "./sites";

describe("filter summary and reset", () => {
  it("says the defaults in one line", () => {
    expect(describeFilters(DEFAULT_FILTERS)).toBe("Filtry domyślne: od 60 MW");
    expect(isDefaultFilters(DEFAULT_FILTERS)).toBe(true);
  });

  it("counts sort as a change for the reset, but not for the filter line", () => {
    const sorted = { ...DEFAULT_FILTERS, sort: "area" as const };
    expect(describeFilters(sorted)).toBe("Filtry domyślne: od 60 MW");
    expect(isDefaultFilters(sorted)).toBe(false);
  });

  it("names a non-default minimum area, which an old link can still set", () => {
    expect(describeFilters({ ...DEFAULT_FILTERS, minMw: 100, minHa: 20 })).toBe("Filtry: od 100 MW, powierzchnia od 20 ha");
  });

  it("keeps the area minimum in the line while the power filter is open", () => {
    expect(describeFilters({ ...DEFAULT_FILTERS, minMw: 0, includeExcluded: true })).toBe(
      "Filtry: moc dowolna, powierzchnia od 10 ha, z wykluczonymi",
    );
  });
});

describe("uniqueBySiteId", () => {
  it("keeps the first row of each site and the order of the rest", () => {
    const rows = [{ site_id: "a", n: 1 }, { site_id: "b", n: 2 }, { site_id: "a", n: 3 }, { site_id: "c", n: 4 }];
    expect(uniqueBySiteId(rows)).toEqual([
      { site_id: "a", n: 1 },
      { site_id: "b", n: 2 },
      { site_id: "c", n: 4 },
    ]);
  });
});

describe("site filters in the URL", () => {
  it("starts from the brief's target (60 MW, 10 ha) when the URL is clean", () => {
    expect(parseFilters(new URLSearchParams())).toEqual(DEFAULT_FILTERS);
    expect(toParams(DEFAULT_FILTERS).toString()).toBe("");
  });

  it("round-trips a non-default filter state", () => {
    const filters = { sort: "area" as const, minMw: 150, minHa: 0, includeExcluded: true, voivodeship: "", powiat: "" };
    const params = toParams(filters);
    expect(params.toString()).toBe("sort=area&minMw=150&minHa=0&excluded=1");
    expect(parseFilters(params)).toEqual(filters);
  });

  it("keeps the place filter in the URL, so a filtered list can be shared", () => {
    const filters = { ...DEFAULT_FILTERS, voivodeship: "14", powiat: "1421" };
    const params = toParams(filters);
    expect(params.toString()).toBe("woj=14&powiat=1421");
    expect(parseFilters(params)).toEqual(filters);
    expect(parseFilters(new URLSearchParams("woj=14"))).toEqual({ ...DEFAULT_FILTERS, voivodeship: "14" });
  });

  it("derives the voivodeship from a powiat and drops codes of the wrong shape", () => {
    expect(parseFilters(new URLSearchParams("powiat=1421")).voivodeship).toBe("14");
    expect(parseFilters(new URLSearchParams("powiat=14&woj=abc"))).toEqual(DEFAULT_FILTERS);
    expect(parseFilters(new URLSearchParams("woj=1421")).voivodeship).toBe("");
  });

  it("names the place in the card and the list as gmina, powiat, województwo", () => {
    const place = { gmina: "Brwinów", powiat: "powiat pruszkowski", voivodeship: "mazowieckie" };
    expect(placeLine(place)).toBe("gm. Brwinów, pow. pruszkowski");
    expect(placeLine(place, true)).toBe("gm. Brwinów, pow. pruszkowski, woj. mazowieckie");
    expect(placeLine({ gmina: null, powiat: "Warszawa", voivodeship: null })).toBe("pow. Warszawa");
    expect(placeLine({ gmina: null, powiat: null, voivodeship: null })).toBe("");
  });

  it("ignores values that are not offered in the UI", () => {
    const parsed = parseFilters(new URLSearchParams("sort=name&minMw=7&minHa=abc"));
    expect(parsed).toEqual(DEFAULT_FILTERS);
  });
});
