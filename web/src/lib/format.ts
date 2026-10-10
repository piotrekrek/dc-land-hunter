/**
 * Polish number and date formatting (brief 8.2): "12,5 ha", "8 MW", "2,3 km", dates "dd.mm.rrrr".
 * A non-breaking space keeps the value and its unit on one line.
 */

const NBSP = "\u00a0";
const TZ = "Europe/Warsaw";

const numberFormats = new Map<number, Intl.NumberFormat>();

export function formatNumber(value: number, maxFractionDigits = 1): string {
  let fmt = numberFormats.get(maxFractionDigits);
  if (!fmt) {
    fmt = new Intl.NumberFormat("pl-PL", { maximumFractionDigits: maxFractionDigits });
    numberFormats.set(maxFractionDigits, fmt);
  }
  return fmt.format(value);
}

function withUnit(value: number | null | undefined, unit: string, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "?";
  return `${formatNumber(value, digits)}${NBSP}${unit}`;
}

export const formatHa = (ha: number | null | undefined) => withUnit(ha, "ha");
export const formatMW = (mw: number | null | undefined) => withUnit(mw, "MW");
export const formatKm = (km: number | null | undefined) => withUnit(km, "km");
export const formatPln = (pln: number | null | undefined) => withUnit(pln, "zł", 0);

export function formatPercent(fraction: number | null | undefined): string {
  if (fraction === null || fraction === undefined || Number.isNaN(fraction)) return "?";
  return `${formatNumber(fraction * 100, 0)}%`;
}

const dateFmt = new Intl.DateTimeFormat("pl-PL", { day: "2-digit", month: "2-digit", year: "numeric", timeZone: TZ });
const timeFmt = new Intl.DateTimeFormat("pl-PL", { hour: "2-digit", minute: "2-digit", timeZone: TZ });

function toDate(value: string | Date | null | undefined): Date | null {
  if (!value) return null;
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "dd.mm.rrrr" in Europe/Warsaw, or an em dash when missing. */
export function formatDate(value: string | Date | null | undefined): string {
  const d = toDate(value);
  return d ? dateFmt.format(d) : "—";
}

/** "dd.mm.rrrr, gg:mm" in Europe/Warsaw. */
export function formatDateTime(value: string | Date | null | undefined): string {
  const d = toDate(value);
  return d ? `${dateFmt.format(d)}, ${timeFmt.format(d)}` : "—";
}
