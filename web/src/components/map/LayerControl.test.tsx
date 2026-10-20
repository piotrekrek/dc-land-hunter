import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LayerControl } from "./LayerControl";

const setViewport = (desktop: boolean) => vi.stubGlobal("matchMedia", (query: string) => ({ matches: desktop, media: query }));

afterEach(() => vi.unstubAllGlobals());

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
});
