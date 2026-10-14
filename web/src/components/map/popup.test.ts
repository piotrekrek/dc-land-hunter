import { describe, expect, it } from "vitest";

import { formatCoordinates, linePopup, osmUrl, parseVoltages, renderPopup, stationPopup } from "./popup";

const sp = (s: string) => s.replace(/\u00a0/g, " ");

describe("parseVoltages", () => {
  it("reads arrays, JSON strings, Postgres array text and single numbers", () => {
    expect(parseVoltages([110000, 15000])).toEqual([110000, 15000]);
    expect(parseVoltages("[110000,15000]")).toEqual([110000, 15000]);
    expect(parseVoltages("{400000,110000}")).toEqual([400000, 110000]);
    expect(parseVoltages(110000)).toEqual([110000]);
  });
  it("gives nothing for missing or unreadable values", () => {
    expect(parseVoltages(undefined)).toEqual([]);
    expect(parseVoltages(null)).toEqual([]);
    expect(parseVoltages("")).toEqual([]);
    expect(parseVoltages([0, "x"])).toEqual([]);
  });
});

describe("stationPopup", () => {
  const props = {
    osm_id: 123,
    name: "GPZ Konin Północ",
    operator: "Enea Operator",
    dso: "enea",
    cls: "GPZ_110_SN",
    confidence: "estimated",
    voltages: "[110000,15000]",
    foreign_operator: false,
  };

  it("describes a GPZ in Polish", () => {
    const m = stationPopup(props, 18.2, 52.2);
    expect(m.title).toBe("GPZ Konin Północ");
    const rows = Object.fromEntries(m.rows.map((r) => [r.label, sp(r.value)]));
    expect(rows["Rodzaj"]).toBe("GPZ 110 kV/SN");
    expect(rows["Napięcia"]).toBe("110 kV, 15 kV");
    expect(rows["Operator"]).toBe("Enea Operator");
    expect(rows["Wiarygodność"]).toBe("szacowane · źródło: OpenStreetMap");
    expect(rows["Położenie"]).toContain("nie pomiar geodezyjny");
    expect(m.coordinates).toBe("52,2; 18,2");
  });

  it("flags a non-utility owner as a possible brownfield", () => {
    const m = stationPopup({ ...props, operator: "KGHM", dso: null, foreign_operator: "true" }, 18, 52);
    expect(m.rows.find((r) => r.label === "Operator")?.value).toContain("obcy operator");
  });

  it("copes with a bare tile: no name, no operator, no voltages", () => {
    const m = stationPopup({ cls: "UNKNOWN_VOLTAGE", confidence: "missing" }, 18, 52);
    expect(m.title).toBe("Stacja bez nazwy");
    expect(m.rows.map((r) => r.label)).toEqual(["Rodzaj", "Wiarygodność", "Położenie"]);
    expect(m.links).toHaveLength(1); // no OSM link without an id
  });

  it("links to Google Maps and to the OSM node or way", () => {
    const node = stationPopup({ ...props, osm_id: 123 }, 18.5, 52.25);
    expect(node.links[0]?.href).toBe("https://www.google.com/maps?q=52.250000,18.500000");
    expect(node.links[1]?.href).toBe("https://www.openstreetmap.org/node/123");
    expect(osmUrl(1_000_000_000_042)).toBe("https://www.openstreetmap.org/way/42");
  });
});

describe("linePopup", () => {
  it("shows voltage and circuits", () => {
    const m = linePopup({ osm_id: 5, voltage: 400000, circuits: 2, confidence: "estimated" }, 17, 51);
    expect(sp(m.title)).toBe("Linia 400 kV");
    expect(m.rows.find((r) => r.label === "Obwody")?.value).toBe("2");
  });
  it("copes with no voltage", () => {
    expect(linePopup({}, 17, 51).title).toBe("Linia WN");
  });
});

describe("renderPopup", () => {
  it("never interprets data as HTML", () => {
    const m = stationPopup(
      {
        name: '<img src=x onerror="alert(1)">',
        operator: "<b>x</b>",
        cls: "GPZ_110_SN",
      },
      18,
      52,
    );
    const el = renderPopup(m);
    expect(el.querySelector("img")).toBeNull();
    expect(el.querySelector("b")).toBeNull();
    expect(el.textContent).toContain('<img src=x onerror="alert(1)">');
  });
  it("opens links in a new tab without opener access", () => {
    const el = renderPopup(stationPopup({ osm_id: 1, cls: "GPZ_110_SN" }, 18, 52));
    for (const a of el.querySelectorAll("a")) {
      expect(a.target).toBe("_blank");
      expect(a.rel).toContain("noopener");
    }
  });
  it("copies the coordinates", async () => {
    const writeText = (s: string) => {
      written.push(s);
      return Promise.resolve();
    };
    const written: string[] = [];
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    const el = renderPopup(stationPopup({ cls: "GPZ_110_SN" }, 18.5, 52.25));
    el.querySelector("button")?.click();
    await Promise.resolve();
    expect(written).toEqual([formatCoordinates(18.5, 52.25)]);
  });
});
