import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

type Handler = (e?: unknown) => void;

const h = vi.hoisted(() => {
  const state = {
    handlers: {} as Record<string, Handler[]>,
    once: {} as Record<string, Handler[]>,
    setStyle: undefined as unknown as ReturnType<typeof vi.fn>,
    resize: undefined as unknown as ReturnType<typeof vi.fn>,
    canvas: { style: { cursor: "" } },
    features: [] as unknown[],
    popups: [] as Array<{
      lngLat: unknown;
      content?: HTMLElement;
      removed: boolean;
    }>,
  };
  return state;
});

vi.mock("maplibre-gl", () => {
  class FakeMap {
    constructor() {
      h.handlers = {};
      h.once = {};
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
    getSource() {
      return undefined;
    }
    getLayer() {
      return {};
    }
    addSource() {}
    addLayer() {}
    remove() {}
    setLayoutProperty() {}
    getCanvas() {
      return h.canvas;
    }
    queryRenderedFeatures() {
      return h.features;
    }
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

  it("opens nothing and closes the old popup when the click hits no feature", () => {
    h.features = [station];
    render(<MapView />);
    act(() => emit("click", click));
    h.features = [];
    act(() => emit("click", click));
    expect(h.popups).toHaveLength(1);
    expect(h.popups[0]?.removed).toBe(true);
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
