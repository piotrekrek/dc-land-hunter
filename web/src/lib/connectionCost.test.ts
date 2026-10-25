import { describe, expect, it } from "vitest";

import { connectionCost, DEFAULT_CONNECTION_RULES } from "./connectionCost";

describe("connectionCost", () => {
  it("60 MW: 60 tys. zł opłaty, 3,6 mln zaliczki, 1,8 mln zabezpieczenia, ok. 5,4 mln zamrożone", () => {
    expect(connectionCost(60)).toEqual({
      fee: 60_000,
      advance: 3_600_000,
      security: 1_800_000,
      frozen: 5_400_000,
      total: 5_460_000,
    });
  });

  it("caps the fee at 100 tys. zł and the advance at 6 mln zł", () => {
    const c = connectionCost(150);
    expect(c.fee).toBe(100_000);
    expect(c.advance).toBe(6_000_000);
  });

  it("charges security on at most 100 MW", () => {
    expect(connectionCost(100).security).toBe(3_000_000);
    expect(connectionCost(250).security).toBe(3_000_000);
  });

  it("is zero for no, negative or invalid power", () => {
    for (const mw of [0, -5, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(connectionCost(mw).total).toBe(0);
    }
  });

  it("takes every figure from the rules", () => {
    const rules = { ...DEFAULT_CONNECTION_RULES, advancePlnPerKw: 10, securityMaxMw: 20, feeCapPln: 5_000 };
    const c = connectionCost(60, rules);
    expect(c).toMatchObject({ fee: 5_000, advance: 600_000, security: 600_000 });
  });
});
