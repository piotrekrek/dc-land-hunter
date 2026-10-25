import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mockFetch } from "@/test/render";

type Handler = (e?: unknown) => void;

const h = vi.hoisted(() => {
  const state = {
    handlers: {} as Record<string, Handler[]>,
    once: {} as Record<string, Handler[]>,
    setStyle: undefined as unknown as ReturnType<typeof vi.fn>,
    resize: undefined as unknown as ReturnType<typeof vi.fn>,
    canvas: { style: { cursor: "" } },
    features: [] as unknown[],
    flyTo: undefined as unknown as ReturnType<typeof vi.fn>,
    easeTo: undefined as unknown as ReturnType<typeof vi.fn>,
    zoom: 5.5,
    markers: [] as Array<{ lngLat: unknown; removed: boolean }>,
    filters: [] as Array<{ layer: string; filter: unknown }>,
    popups: [] as Array<{
      lngLat: unknown;
      content?: HTMLElement;
      removed: boolean;
    }>,
    created: [] as Array<{ style: unknown }>,
    layout: [] as unknown[][],
    paint: [] as unknown[][],
    coverage: [] as unknown[],
  };
  return state;
});

vi.mock("maplibre-gl", () => {
  class FakeMap {
    constructor(options: { style: unknown }) {
      h.handlers = {};
      h.once = {};
      h.created.push(options);
    }
    on(ev: string, fn: Handler) {
      (h.handlers[ev] ??= []).push(fn);
    }
    once(ev: string, fn: Handler) {
      (h.once[ev] ??= []).push(fn);
    }
    addControl() {}
    setStyle(s: unknown) {
      h.setStyle(s);
    }
    resize() {
      h.resize();
    }
    getSource(id: string) {
      return id === "coverage" ? { setData: (data: unknown) => h.coverage.push(data) } : undefined;
    }
    getLayer() {
      return {};
    }
    addSource() {}
    addLayer() {}
    remove() {}
    setLayoutProperty(...args: unknown[]) {
      h.layout.push(args);
    }
    setPaintProperty(...args: unknown[]) {
      h.paint.push(args);
    }
    setFilter(layer: string, filter: unknown) {
      h.filters.push({ layer, filter });
    }
    getCanvas() {
      return h.canvas;
    }
    queryRenderedFeatures() {
      return h.features;
    }
    flyTo(options: unknown) {
      h.flyTo(options);
    }
    easeTo(options: unknown) {
      h.easeTo(options);
    }
    getZoom() {
      return h.zoom;
    }
  }
  class FakeMarker {
    setLngLat(ll: unknown) {
      this.lngLat = ll;
      h.markers.push(this);
      return this;
    }
    addTo() {
      return this;
    }
    remove() {
      this.removed = true;
    }
    lngLat: unknown;
    removed = false;
  }
  class FakePopup {
    constructor() {
      h.popups.push(this);
    }
    setLngLat(ll: unknown) {
      this.lngLat = ll;
      return this;
    }
    setDOMContent(el: HTMLElement) {
      this.content = el;
      return this;
    }
    addTo() {
      return this;
    }
    remove() {
      this.removed = true;
    }
    lngLat: unknown;
    content?: HTMLElement;
    removed = false;
  }
  return {
    default: {
      Map: FakeMap,
      Popup: FakePopup,
      Marker: FakeMarker,
      NavigationControl: class {},
      ScaleControl: class {},
    },
  };
});

import { MapView } from "./MapView";

const emit = (ev: string, payload?: unknown) => {
  (h.handlers[ev] ?? []).forEach((fn) => fn(payload));
  (h.once[ev] ?? []).forEach((fn) => fn(payload));
  h.once[ev] = [];
};

describe("MapView basemap fallback", () => {
  beforeEach(() => {
    h.setStyle = vi.fn();
    h.resize = vi.fn();
  });

  it("keeps the basemap when a tile error arrives after the style loaded", () => {
    render(<MapView />);
    act(() => emit("style.load"));
    act(() => emit("error", { error: new Error("404"), sourceId: "lines" }));
    expect(h.setStyle).not.toHaveBeenCalled();
    expect(screen.queryByText(/Podkład mapowy niedostępny/)).not.toBeInTheDocument();
  });

  it("reports basemap tile errors after the style loaded", () => {
    render(<MapView />);
    act(() => emit("style.load"));
    act(() =>
      emit("error", {
        error: new Error("Failed to fetch"),
        sourceId: "openmaptiles",
      }),
    );
    expect(screen.getByText(/ładuje się z błędami: Failed to fetch/)).toBeInTheDocument();
    expect(h.setStyle).not.toHaveBeenCalled();
  });

  it("falls back when the basemap style fails to load", () => {
    render(<MapView />);
    act(() => emit("error", { error: new Error("style 403") }));
    expect(h.setStyle).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Podkład mapowy niedostępny/)).toBeInTheDocument();
  });

  it("falls back when the basemap hangs", () => {
    vi.useFakeTimers();
    render(<MapView />);
    act(() => {
      vi.advanceTimersByTime(8001);
    });
    vi.useRealTimers();
    expect(h.setStyle).toHaveBeenCalledTimes(1);
  });
});

describe("MapView theme", () => {
  const LIGHT = "https://tiles.openfreemap.org/styles/liberty";
  const DARK = "https://tiles.openfreemap.org/styles/dark";
  const bgOf = (style: unknown) => (style as { layers: Array<{ paint: Record<string, string> }> }).layers[0]!.paint["background-color"];

  // Toggling the theme changes <html class>; the MutationObserver callback runs as a microtask.
  const setDark = async (dark: boolean) => {
    await act(async () => {
      document.documentElement.classList.toggle("dark", dark);
    });
  };

  beforeEach(() => {
    h.setStyle = vi.fn();
    h.resize = vi.fn();
    h.created = [];
    h.layout = [];
    h.paint = [];
  });

  afterEach(async () => {
    await setDark(false);
  });

  it("starts with the light basemap on a light page and the dark one on a dark page", () => {
    const { unmount } = render(<MapView />);
    expect(h.created[0]!.style).toBe(LIGHT);
    unmount();
    localStorage.setItem("dclh-theme", "dark");
    render(<MapView />);
    expect(h.created[1]!.style).toBe(DARK);
    localStorage.removeItem("dclh-theme");
  });

  it("switches the basemap with the theme and re-adds the grid layers after each style load", async () => {
    render(<MapView />);
    act(() => emit("style.load"));
    h.layout = [];
    await setDark(true);
    expect(h.setStyle).toHaveBeenLastCalledWith(DARK);
    act(() => emit("style.load"));
    // Dark only: the grid layers lighten the buildings of the dark basemap.
    expect(h.paint.some((args) => args[0] === "building")).toBe(true);
    expect(h.layout.some((args) => args[1] === "visibility")).toBe(true);

    h.paint = [];
    h.layout = [];
    await setDark(false);
    expect(h.setStyle).toHaveBeenLastCalledWith(LIGHT);
    act(() => emit("style.load"));
    expect(h.paint).toHaveLength(0);
    expect(h.layout.some((args) => args[1] === "visibility")).toBe(true);
  });

  it("does not reload the style when the theme is unchanged", async () => {
    render(<MapView />);
    await setDark(false);
    expect(h.setStyle).not.toHaveBeenCalled();
  });

  it("uses a fallback background in the current theme", async () => {
    render(<MapView />);
    act(() => emit("error", { error: new Error("style 403") }));
    expect(bgOf(h.setStyle.mock.calls[0]![0])).toBe("#e9edf1");
    await setDark(true);
    expect(bgOf(h.setStyle.mock.lastCall![0])).toBe("#1b1f26");
  });
});

describe("MapView resizing", () => {
  it("resizes the map when its container changes size", () => {
    h.resize = vi.fn();
    let cb: () => void = () => {};
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(fn: () => void) {
          cb = fn;
        }
        observe() {}
        disconnect() {}
      },
    );
    render(<MapView />);
    cb();
    expect(h.resize).toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});

describe("MapView click popups", () => {
  const station = {
    layer: { id: "substations-circle" },
    properties: {
      name: "GPZ Test",
      cls: "GPZ_110_SN",
      osm_id: 7,
      voltages: "[110000,15000]",
    },
    geometry: { type: "Point", coordinates: [18.5, 52.25] },
  };
  const point = { x: 10, y: 10 };
  const click = { point, lngLat: { lng: 1, lat: 2 } };

  beforeEach(() => {
    h.setStyle = vi.fn();
    h.resize = vi.fn();
    h.popups = [];
    h.features = [];
    h.canvas.style.cursor = "";
  });

  it("opens a popup at the station's own position, not at the cursor", () => {
    h.features = [station];
    render(<MapView />);
    act(() => emit("style.load"));
    act(() => emit("click", click));
    expect(h.popups).toHaveLength(1);
    expect(h.popups[0]?.lngLat).toEqual([18.5, 52.25]);
    expect(h.popups[0]?.content?.textContent).toContain("GPZ Test");
  });

  it("opens the station popup for an unknown-voltage station too", () => {
    h.features = [{ ...station, layer: { id: "substations-unknown-circle" }, properties: { name: "Stacja X", cls: "UNKNOWN_VOLTAGE", osm_id: 8 } }];
    render(<MapView />);
    act(() => emit("style.load"));
    act(() => emit("click", click));
    expect(h.popups).toHaveLength(1);
    expect(h.popups[0]?.lngLat).toEqual([18.5, 52.25]);
    expect(h.popups[0]?.content?.textContent).toContain("Stacja X");
  });

  it("prefers a station over a line under the same click", () => {
    h.features = [
      {
        layer: { id: "lines-110" },
        properties: { voltage: 110000 },
        geometry: { type: "LineString" },
      },
      station,
    ];
    render(<MapView />);
    act(() => emit("click", click));
    expect(h.popups[0]?.content?.textContent).toContain("GPZ Test");
  });

  it("opens a line popup at the click position", () => {
    h.features = [
      {
        layer: { id: "lines-400" },
        properties: { voltage: 400000 },
        geometry: { type: "LineString" },
      },
    ];
    render(<MapView />);
    act(() => emit("click", click));
    expect(h.popups[0]?.lngLat).toEqual([1, 2]);
    expect(h.popups[0]?.content?.textContent).toContain("Linia");
  });

  it("closes the old popup and offers a parcel check when the click hits no feature", () => {
    h.features = [station];
    render(<MapView />);
    act(() => emit("click", click));
    h.features = [];
    act(() => emit("click", click));
    expect(h.popups).toHaveLength(2);
    expect(h.popups[0]?.removed).toBe(true);
    expect(h.popups[1]?.lngLat).toEqual([1, 2]);
    const link = h.popups[1]?.content?.querySelector("a[href^='/sprawdz-dzialke']");
    expect(link?.textContent).toBe("Sprawdź działkę w tym miejscu");
    expect(link?.getAttribute("href")).toBe("/sprawdz-dzialke?q=2.00000%201.00000");
  });

  it("shows a pointer cursor only over a feature", () => {
    h.features = [station];
    render(<MapView />);
    act(() => emit("mousemove", { point }));
    expect(h.canvas.style.cursor).toBe("pointer");
    h.features = [];
    act(() => emit("mousemove", { point }));
    expect(h.canvas.style.cursor).toBe("");
  });
});

describe("MapView focus", () => {
  beforeEach(() => {
    h.setStyle = vi.fn();
    h.resize = vi.fn();
    h.flyTo = vi.fn();
    h.markers = [];
  });

  it("flies to the focused site and marks it", () => {
    render(<MapView focus={{ siteId: "site-a", lon: 20.9, lat: 52.1 }} />);
    expect(h.flyTo).toHaveBeenCalledWith({ center: [20.9, 52.1], zoom: 15 });
    expect(h.markers).toHaveLength(1);
    expect(h.markers[0]!.lngLat).toEqual([20.9, 52.1]);
  });

  it("moves the marker when another site is picked, and flies again for the same site", () => {
    const { rerender } = render(<MapView focus={{ siteId: "site-a", lon: 20.9, lat: 52.1 }} />);
    rerender(<MapView focus={{ siteId: "site-b", lon: 17.0, lat: 51.1 }} />);
    expect(h.markers[0]!.removed).toBe(true);
    expect(h.flyTo).toHaveBeenLastCalledWith({ center: [17.0, 51.1], zoom: 15 });
    rerender(<MapView focus={{ siteId: "site-b", lon: 17.0, lat: 51.1 }} />);
    expect(h.flyTo).toHaveBeenCalledTimes(3);
  });

  it("does nothing without a focus", () => {
    render(<MapView />);
    expect(h.flyTo).not.toHaveBeenCalled();
  });
});

describe("MapView site selection", () => {
  const site = {
    layer: { id: "sites-fill" },
    properties: { site_id: "site-a", score: 81, excluded: false, area_ha: 12.5 },
    geometry: { type: "Polygon" },
  };
  const point = { x: 10, y: 10 };
  const click = { point, lngLat: { lng: 20.9, lat: 52.1 } };

  beforeEach(() => {
    h.setStyle = vi.fn();
    h.resize = vi.fn();
    h.flyTo = vi.fn();
    h.easeTo = vi.fn();
    h.zoom = 5.5;
    h.popups = [];
    h.features = [];
    h.filters = [];
    h.canvas.style.cursor = "";
  });

  const bubble = {
    layer: { id: "site-clusters" },
    properties: { n: 1341, best: 62 },
    geometry: { type: "Point", coordinates: [15.57, 51.26] },
  };

  it("zooms into a bubble instead of picking a site or opening a popup", () => {
    const onSiteClick = vi.fn();
    h.features = [bubble];
    render(<MapView onSiteClick={onSiteClick} />);
    act(() => emit("click", click));
    expect(h.easeTo).toHaveBeenCalledWith({ center: [15.57, 51.26], zoom: 7.5 });
    expect(onSiteClick).not.toHaveBeenCalled();
    expect(h.popups).toHaveLength(0);
  });

  it("shows a pointer over a bubble", () => {
    h.features = [bubble];
    render(<MapView />);
    act(() => emit("mousemove", { point }));
    expect(h.canvas.style.cursor).toBe("pointer");
  });

  it("picks the site under a click, with no popup", () => {
    const onSiteClick = vi.fn();
    h.features = [site];
    render(<MapView onSiteClick={onSiteClick} />);
    act(() => emit("click", click));
    expect(onSiteClick).toHaveBeenCalledWith({ siteId: "site-a", lon: 20.9, lat: 52.1 });
    expect(h.popups).toHaveLength(0);
  });

  it("keeps the line popup when a line passes over a site", () => {
    const onSiteClick = vi.fn();
    h.features = [
      {
        layer: { id: "lines-110" },
        properties: { voltage: 110000 },
        geometry: { type: "LineString" },
      },
      site,
    ];
    render(<MapView onSiteClick={onSiteClick} />);
    act(() => emit("click", click));
    expect(onSiteClick).not.toHaveBeenCalled();
    expect(h.popups).toHaveLength(1);
  });

  it("outlines the focused site", () => {
    const { rerender } = render(<MapView focus={{ siteId: "site-a", lon: 20.9, lat: 52.1 }} />);
    expect(h.filters.at(-1)).toEqual({ layer: "sites-selected", filter: ["==", ["get", "site_id"], "site-a"] });
    rerender(<MapView focus={{ siteId: "site-b", lon: 17.0, lat: 51.1 }} />);
    expect(h.filters.at(-1)).toEqual({ layer: "sites-selected", filter: ["==", ["get", "site_id"], "site-b"] });
  });
});

describe("MapView coverage layer", () => {
  const area = {
    teryt: "1421011",
    name: "Piaseczno",
    sites: 4,
    with_data: 3,
    total: 4,
    share: 0.75,
    by_item: { zoning: 0.5, pen_soil_iii: 0, bonus_psi: 1, grid_headroom: 0.25 },
    geometry: { type: "Polygon", coordinates: [[[20, 52], [21, 52], [21, 53], [20, 52]]] },
  };

  beforeEach(() => {
    h.setStyle = vi.fn();
    h.resize = vi.fn();
    h.coverage = [];
  });

  afterEach(() => vi.restoreAllMocks());

  it("fetches the gmina coverage with shapes when the layer is switched on, and draws it", async () => {
    const fetchSpy = mockFetch({ "/api/coverage": { level: "gmina", item: "all", areas: [area, { ...area, teryt: "1425011", geometry: null }] } });
    render(<MapView />);
    await userEvent.click(screen.getByLabelText("Pokrycie danych"));

    await waitFor(() => expect(h.coverage.length).toBeGreaterThan(0));
    const url = new URL(requestUrl(fetchSpy.mock.calls[0]![0]));
    expect(url.pathname).toBe("/api/coverage");
    expect(Object.fromEntries(url.searchParams)).toEqual({ level: "gmina", item: "all", include_geometry: "true" });
    const drawn = h.coverage.at(-1) as { features: { properties: { share: number } }[] };
    expect(drawn.features.map((f) => f.properties.share)).toEqual([0.75]);
  });

  it("refetches for the chosen level and item", async () => {
    const fetchSpy = mockFetch({ "/api/coverage": { level: "gmina", item: "all", areas: [area] } });
    render(<MapView />);
    await userEvent.click(screen.getByLabelText("Pokrycie danych"));
    await userEvent.selectOptions(screen.getByLabelText("Wskaźnik"), "pen_soil_iii");

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2));
    const last = new URL(requestUrl(fetchSpy.mock.calls[1]![0]));
    expect(last.searchParams.get("item")).toBe("pen_soil_iii");
    expect(last.searchParams.get("level")).toBe("gmina");
  });

  it("says so when the coverage cannot be loaded", async () => {
    mockFetch({});
    render(<MapView />);
    await userEvent.click(screen.getByLabelText("Pokrycie danych"));
    expect(await screen.findByText("Nie udało się wczytać pokrycia danych.")).toBeInTheDocument();
  });

  it("does not ask the API while the layer is off", () => {
    const fetchSpy = mockFetch({ "/api/coverage": { level: "gmina", item: "all", areas: [] } });
    render(<MapView />);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Poziom obszaru")).not.toBeInTheDocument();
  });
});

function requestUrl(input: unknown): string {
  return typeof input === "string" ? input : input instanceof URL ? input.href : (input as Request).url;
}
