import { describe, expect, it } from "vitest";

import { DEFAULT_FILTERS, parseFilters, toParams } from "./sites";

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
