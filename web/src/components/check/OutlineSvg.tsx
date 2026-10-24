import type { ResolvedParcel } from "@/api/client";

/** The parcel outline from its WGS84 ring, scaled to the drawing: enough to ask "is this the parcel I meant?". */
export function OutlineSvg({ parcel }: { parcel: ResolvedParcel }) {
  const ring = ((parcel.outline.coordinates as number[][][])[0] ?? []) as number[][];
  const cos = Math.cos((parcel.lat * Math.PI) / 180);
  const points = ring.map(([lon, lat]) => [(lon! - parcel.lon) * cos, -(lat! - parcel.lat)] as const);
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const pad = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) * 0.2 || 1e-4;
  const x0 = Math.min(...xs) - pad;
  const y0 = Math.min(...ys) - pad;
  const w = Math.max(...xs) + pad - x0;
  const h = Math.max(...ys) + pad - y0;
  return (
    <svg viewBox={`${x0} ${y0} ${w} ${h}`} role="img" aria-label={`Obrys działki ${parcel.parcel_id}`} className="h-36 w-full border border-ink bg-paper">
      <path d={`M${points.map((p) => p.join(" ")).join("L")}Z`} fill="var(--accent-soft)" fillOpacity="0.55" stroke="var(--ink)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
