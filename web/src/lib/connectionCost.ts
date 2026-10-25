/**
 * What securing a grid connection ties up, per the Prawo energetyczne amendment of 30.04.2026 (CLAUDE.md section 3).
 * These are legal interpretations that need verification (risk R8), so every figure is data in `ConnectionRules`,
 * never inline arithmetic, and the UI labels the result "wymaga weryfikacji prawnej".
 */
export interface ConnectionRules {
  /** Application fee, PLN per kW of requested power, and its cap. */
  feePlnPerKw: number;
  feeCapPln: number;
  /** Connection advance, PLN per kW, and its cap. */
  advancePlnPerKw: number;
  advanceCapPln: number;
  /** Security, PLN per kW, charged on at most this many MW ("30 PLN/kW up to 100 MW"). */
  securityPlnPerKw: number;
  securityMaxMw: number;
}

export const DEFAULT_CONNECTION_RULES: ConnectionRules = {
  feePlnPerKw: 1,
  feeCapPln: 100_000,
  advancePlnPerKw: 60,
  advanceCapPln: 6_000_000,
  securityPlnPerKw: 30,
  securityMaxMw: 100,
};

export interface ConnectionCost {
  fee: number;
  advance: number;
  security: number;
  /** Advance plus security: the money tied up while the project waits. The fee is spent, not frozen. */
  frozen: number;
  /** Fee plus frozen money. */
  total: number;
}

/** Costs for `mw` megawatts. A non-finite or non-positive power costs nothing. */
export function connectionCost(mw: number, rules: ConnectionRules = DEFAULT_CONNECTION_RULES): ConnectionCost {
  const kw = Number.isFinite(mw) && mw > 0 ? mw * 1000 : 0;
  const fee = Math.min(kw * rules.feePlnPerKw, rules.feeCapPln);
  const advance = Math.min(kw * rules.advancePlnPerKw, rules.advanceCapPln);
  const security = Math.min(kw, rules.securityMaxMw * 1000) * rules.securityPlnPerKw;
  return { fee, advance, security, frozen: advance + security, total: fee + advance + security };
}
