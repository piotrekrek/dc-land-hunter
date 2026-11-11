import { describe, expect, it } from "vitest";

import { planningRows } from "./planningPath";

const fact = (value: string | number) => ({ value, source_id: "ru", as_of: "2026-11-11T08:00:00+00:00", confidence: "verified" as const });

describe("planningRows", () => {
  it("shows nothing when the register has no plan ogólny for the site", () => {
    expect(planningRows({})).toEqual([]);
  });

  it("lists the Rzepin case: outside the OUZ, MPZP or ZPI, zone limits", () => {
    const rows = planningRows({
      in_ouz: fact("no"),
      planning_path: fact("mpzp_or_zpi"),
      plan_ogolny_act: fact("XXXIV/224/2026"),
      plan_ogolny_from: fact("2026-09-11"),
      zone_max_height_m: fact(20),
      zone_max_build_up_pct: fact(70),
      zone_min_pbc_pct: fact(20),
    });
    expect(rows).toEqual([
      { label: "W obszarze uzupełnienia zabudowy (OUZ)", value: "nie" },
      { label: "Ścieżka planistyczna", value: "potrzebny MPZP lub ZPI" },
      { label: "Plan ogólny", value: "uchwała XXXIV/224/2026, obowiązuje od 11.09.2026" },
      { label: "Maks. wysokość zabudowy", value: "20 m" },
      { label: "Maks. udział powierzchni zabudowy", value: "70%" },
      { label: "Min. powierzchnia biologicznie czynna", value: "20%" },
    ]);
  });
});
