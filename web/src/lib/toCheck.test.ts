import { describe, expect, it } from "vitest";

import { GENERIC_WHO_TO_ASK, toCheck, WHO_TO_ASK } from "./toCheck";

const item = (key: string, points: number, upside: number, confidence: "verified" | "estimated" | "missing" = "missing") => ({
  key,
  label_pl: key,
  points,
  upside_points: upside,
  confidence,
});

describe("toCheck", () => {
  it("lists only items without data, the biggest gain first", () => {
    const list = toCheck([
      item("land_size", 30, 35, "verified"),
      item("conn_city", 0, 5),
      item("zoning", 10, 25),
      item("pen_gas", 0, 0),
    ]);
    expect(list.map((e) => e.key)).toEqual(["zoning", "conn_city", "pen_gas"]);
    expect(list.map((e) => e.gain)).toEqual([15, 5, 0]);
  });

  it("never shows a negative gain", () => {
    expect(toCheck([item("pen_gas", 0, -3)])[0]?.gain).toBe(0);
  });

  it("says who to ask, with a generic line for an unknown key", () => {
    const [known, unknown] = toCheck([item("zoning", 0, 5), item("brand_new", 0, 1)]);
    expect(known?.who).toBe(WHO_TO_ASK.zoning);
    expect(unknown?.who).toBe(GENERIC_WHO_TO_ASK);
  });

  it("is empty when nothing is missing", () => {
    expect(toCheck([item("zoning", 20, 20, "verified")])).toEqual([]);
  });
});
