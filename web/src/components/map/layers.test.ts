import { describe, expect, it } from "vitest";

import {
  LAYER_GROUPS,
  LINE_BANDS,
  LINE_LAYER_IDS,
  SITE_COLORS,
  SITE_FILL_ID,
  SITE_HIGHLIGHT_ID,
  SITE_SCORE_OK,
  SITE_SCORE_WARN,
  siteFocus,
  siteHighlightFilter,
  siteTone,
} from "./layers";

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

describe("candidate site layers", () => {
  const sitesGroup = LAYER_GROUPS.find((g) => g.id === "sites");

  it("is one checkbox, on by default, covering the fill, outline and highlight", () => {
    expect(sitesGroup?.label).toBe("Kandydaci (działki)");
    expect(sitesGroup?.defaultOn).toBe(true);
    expect(sitesGroup?.layerIds).toEqual([SITE_FILL_ID, "sites-line", SITE_HIGHLIGHT_ID]);
  });

  it("has a legend entry for every tone, in Polish", () => {
    expect(sitesGroup?.legend?.map((item) => item.color)).toEqual([
      SITE_COLORS.ok,
      SITE_COLORS.warn,
      SITE_COLORS.bad,
      SITE_COLORS.muted,
    ]);
    expect(sitesGroup?.legend?.map((item) => item.label)).toEqual([
      "Wynik 70 lub więcej",
      "Wynik 40–69",
      "Wynik poniżej 40",
      "Wykluczone",
    ]);
  });

  it("colours a site by its score, and excluded sites grey whatever the score", () => {
    expect(siteTone(SITE_SCORE_OK, false)).toBe("ok");
    expect(siteTone(SITE_SCORE_OK - 0.1, false)).toBe("warn");
    expect(siteTone(SITE_SCORE_WARN, false)).toBe("warn");
    expect(siteTone(SITE_SCORE_WARN - 0.1, false)).toBe("bad");
    expect(siteTone(95, true)).toBe("muted");
    expect(siteTone(0, true)).toBe("muted");
  });

  it("highlights only the selected site, and nothing when no site is selected", () => {
    expect(siteHighlightFilter("site-a")).toEqual(["==", ["get", "site_id"], "site-a"]);
    expect(siteHighlightFilter(null)).toEqual(["==", ["get", "site_id"], ""]);
  });
});

describe("site click", () => {
  it("selects the clicked site with the click position", () => {
    expect(siteFocus({ site_id: "site-a", score: 80 }, { lng: 20.9, lat: 52.1 })).toEqual({
      siteId: "site-a",
      lon: 20.9,
      lat: 52.1,
    });
  });

  it("selects nothing for a feature without a site id", () => {
    expect(siteFocus({ score: 80 }, { lng: 20.9, lat: 52.1 })).toBeNull();
    expect(siteFocus({ site_id: "" }, { lng: 20.9, lat: 52.1 })).toBeNull();
    expect(siteFocus({ site_id: 7 }, { lng: 20.9, lat: 52.1 })).toBeNull();
  });
});
