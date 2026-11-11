import { formatDate, formatNumber } from "@/lib/format";
import type { SiteDetail } from "@/lib/sites";

type Facts = SiteDetail["features"];

const IN_OUZ: Record<string, string> = { yes: "tak", no: "nie", partial: "częściowo" };
const PATH: Record<string, string> = {
  mpzp_in_force: "MPZP obowiązuje",
  mpzp_or_zpi: "potrzebny MPZP lub ZPI",
  wz_possible: "WZ możliwa",
};

export interface PlanningRow {
  label: string;
  value: string;
}

/**
 * The rows of "Ścieżka planistyczna" (OUZ, route, plan act, zone limits), or [] when the register has no plan
 * ogólny for the site: the card then says nothing, because a missing plan is "do sprawdzenia", not "bad".
 */
export function planningRows(features: Facts): PlanningRow[] {
  const path = features.planning_path?.value;
  if (typeof path !== "string") return [];
  const rows: PlanningRow[] = [];
  const ouz = features.in_ouz?.value;
  if (typeof ouz === "string") rows.push({ label: "W obszarze uzupełnienia zabudowy (OUZ)", value: IN_OUZ[ouz] ?? ouz });
  rows.push({ label: "Ścieżka planistyczna", value: PATH[path] ?? path });
  const act = features.plan_ogolny_act?.value;
  if (act !== undefined) {
    const from = features.plan_ogolny_from?.value;
    const when = typeof from === "string" ? `, obowiązuje od ${formatDate(from)}` : "";
    rows.push({ label: "Plan ogólny", value: `uchwała ${act}${when}` });
  }
  const num = (k: string) => {
    const v = features[k]?.value;
    return typeof v === "number" ? v : null;
  };
  const height = num("zone_max_height_m");
  const buildUp = num("zone_max_build_up_pct");
  const pbc = num("zone_min_pbc_pct");
  const intensity = num("zone_max_intensity");
  if (height !== null) rows.push({ label: "Maks. wysokość zabudowy", value: `${formatNumber(height)} m` });
  if (buildUp !== null) rows.push({ label: "Maks. udział powierzchni zabudowy", value: `${formatNumber(buildUp, 0)}%` });
  if (pbc !== null) rows.push({ label: "Min. powierzchnia biologicznie czynna", value: `${formatNumber(pbc, 0)}%` });
  if (intensity !== null) rows.push({ label: "Maks. intensywność zabudowy", value: formatNumber(intensity, 2) });
  return rows;
}
