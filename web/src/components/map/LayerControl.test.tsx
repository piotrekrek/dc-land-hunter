import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LayerControl } from "./LayerControl";

const setViewport = (desktop: boolean) => vi.stubGlobal("matchMedia", (query: string) => ({ matches: desktop, media: query }));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("LayerControl", () => {
  it("is collapsed below the lg breakpoint, so the panel does not cover the phone map", async () => {
    setViewport(false);
    render(<LayerControl visible={{}} onToggle={() => {}} />);
    const toggle = screen.getByRole("button", { name: "Warstwy mapy" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();

    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByRole("checkbox").length).toBeGreaterThan(0);

    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("is open by default on a desktop", () => {
    setViewport(true);
    render(<LayerControl visible={{}} onToggle={() => {}} />);
    expect(screen.getByRole("button", { name: "Warstwy mapy" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByRole("checkbox").length).toBeGreaterThan(0);
  });

  it("reports a toggled layer while the panel is open", async () => {
    setViewport(true);
    const onToggle = vi.fn();
    render(<LayerControl visible={{}} onToggle={onToggle} />);
    const [first] = screen.getAllByRole("checkbox");
    await userEvent.click(first!);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("remembers the panel as the user left it, over the breakpoint default", async () => {
    setViewport(true);
    const first = render(<LayerControl visible={{}} onToggle={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Warstwy mapy" }));
    expect(localStorage.getItem("dclh.layersOpen")).toBe("0");
    first.unmount();
    render(<LayerControl visible={{}} onToggle={() => {}} />);
    expect(screen.getByRole("button", { name: "Warstwy mapy" })).toHaveAttribute("aria-expanded", "false");
  });

  it("still works when storage is blocked", async () => {
    setViewport(true);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    render(<LayerControl visible={{}} onToggle={() => {}} />);
    const toggle = screen.getByRole("button", { name: "Warstwy mapy" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("puts the lines and stations under one grid switch that disables them while off", () => {
    setViewport(true);
    render(<LayerControl visible={{ grid: false }} onToggle={() => {}} />);
    expect(screen.getByRole("checkbox", { name: "Sieć energetyczna" })).not.toBeChecked();
    for (const name of [/Linie 110 kV/, /Linie 400 kV/, /Stacje \(GPZ, węzły\)/]) {
      expect(screen.getByRole("checkbox", { name })).toBeDisabled();
    }
    expect(screen.getByRole("checkbox", { name: /Kandydaci/ })).toBeEnabled();
  });
});
