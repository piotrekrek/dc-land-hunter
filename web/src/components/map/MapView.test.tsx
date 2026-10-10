import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

type Handler = (e?: unknown) => void;

const h = vi.hoisted(() => {
  const state = {
    handlers: {} as Record<string, Handler[]>,
    once: {} as Record<string, Handler[]>,
    setStyle: undefined as unknown as ReturnType<typeof vi.fn>,
    resize: undefined as unknown as ReturnType<typeof vi.fn>,
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
      return undefined;
    }
    addSource() {}
    addLayer() {}
    remove() {}
  }
  return {
    default: { Map: FakeMap, NavigationControl: class {}, ScaleControl: class {} },
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
    act(() => emit("error", { error: new Error("Failed to fetch"), sourceId: "openmaptiles" }));
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
