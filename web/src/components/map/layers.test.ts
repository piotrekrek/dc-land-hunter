import { describe, expect, it } from "vitest";

import { LAYER_GROUPS, LINE_BANDS, LINE_LAYER_IDS } from "./layers";

// Distinct line voltages in the real OSM snapshot (lines of 110 kV and above; counts in the loader's report).
const REAL_VOLTAGES = [110_000, 220_000, 380_000, 400_000, 450_000];

describe("line voltage bands", () => {
  it("puts every real voltage into exactly one band", () => {
    for (const volts of REAL_VOLTAGES) {
      const bands = LINE_BANDS.filter((b) => volts >= b.min && volts < b.max).map((b) => b.id);
      expect(bands, `${volts} V`).toHaveLength(1);
    }
  });

  it("sends 380 kV and 450 kV lines to the 400 kV band", () => {
    const band = (volts: number) => LINE_BANDS.find((b) => volts >= b.min && volts < b.max)?.id;
    expect(band(380_000)).toBe("lines-400");
    expect(band(450_000)).toBe("lines-400");
    expect(band(220_000)).toBe("lines-220");
    expect(band(110_000)).toBe("lines-110");
  });

  it("gives each voltage its own checkbox, all on by default, with a matching swatch", () => {
    const lineGroups = LAYER_GROUPS.filter((g) => LINE_LAYER_IDS.includes(g.id));
    expect(lineGroups.map((g) => g.label)).toEqual(["Linie 110 kV", "Linie 220 kV", "Linie 400 kV"]);
    for (const band of LINE_BANDS) {
      const group = lineGroups.find((g) => g.id === band.id);
      expect(group?.defaultOn).toBe(true);
      expect(group?.layerIds).toEqual([band.id]);
      expect(group?.color).toBe(band.color);
    }
  });
});
