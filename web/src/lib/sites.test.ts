import { describe, expect, it } from "vitest";

import { DEFAULT_FILTERS, describeFilters, isDefaultFilters, parseFilters, toParams, uniqueBySiteId } from "./sites";

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
    const filters = { sort: "area" as const, minMw: 150, minHa: 0, includeExcluded: true };
    const params = toParams(filters);
    expect(params.toString()).toBe("sort=area&minMw=150&minHa=0&excluded=1");
    expect(parseFilters(params)).toEqual(filters);
  });

  it("ignores values that are not offered in the UI", () => {
    const parsed = parseFilters(new URLSearchParams("sort=name&minMw=7&minHa=abc"));
    expect(parsed).toEqual(DEFAULT_FILTERS);
  });
});
