import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { sheet } from "@/test/checkFixtures";

import { Sheet } from "./Sheet";

describe("Sheet", () => {
  it("draws the parcel and the layers that have paths, north up", () => {
    const { container } = render(<Sheet sheet={sheet} />);
    expect(container.querySelector('[data-layer="parcel"]')).toBeInTheDocument();
    const flood = container.querySelector('[data-layer="flood"]')!;
    // Local metres, north up: the SVG flips the vertical axis, so y = 100 is drawn at -100.
    expect(flood.getAttribute("d")).toContain("L200 -100");
    expect(container.querySelector('[data-layer="housing"]')).toBeInTheDocument();
    expect(container.querySelector('[data-layer="forest"]')).not.toBeInTheDocument(); // too big to draw
  });

  it("describes the drawing in words, so it is never the only carrier of the information", () => {
    render(<Sheet sheet={sheet} />);
    const label = screen.getByRole("img").getAttribute("aria-label")!;
    expect(label).toContain("Strefa zalewowa 1%");
    expect(label).toContain("Budynki mieszkalne");
    expect(label).not.toContain("Las"); // a layer that is not drawn is not claimed
  });

  it("has a toggle per layer; switching one off removes it from the drawing", async () => {
    const user = userEvent.setup();
    const { container } = render(<Sheet sheet={sheet} />);
    const toggles = within(screen.getByRole("group", { name: "Warstwy rysunku" }));
    const flood = toggles.getByRole("button", { name: /Strefa zalewowa 1%/ });
    expect(flood).toHaveAttribute("aria-pressed", "true");
    await user.click(flood);
    expect(flood).toHaveAttribute("aria-pressed", "false");
    expect(container.querySelector('[data-layer="flood"]')).not.toBeInTheDocument();
    await user.click(flood);
    expect(container.querySelector('[data-layer="flood"]')).toBeInTheDocument();
  });

  it("lists a layer that was too big as not drawn, and cannot be toggled", () => {
    render(<Sheet sheet={sheet} />);
    const forest = screen.getByRole("button", { name: /Las/ });
    expect(forest).toBeDisabled();
    expect(forest).toHaveTextContent("nie narysowana");
  });

  it("does not offer a layer that is simply empty here", () => {
    render(<Sheet sheet={sheet} />);
    expect(screen.queryByRole("button", { name: /Obszar górniczy/ })).not.toBeInTheDocument();
  });

  it("draws a scale bar that is a round length for the width of the sheet", () => {
    render(<Sheet sheet={sheet} />);
    expect(screen.getByTestId("scale-label")).toHaveTextContent("250 m"); // 1100 m wide: a quarter is 275 m
  });
});

describe("Sheet orthophoto", () => {
  const photoUrl = "/api/checks/abc/sites/0/orthophoto";

  it("offers no toggle without a photo address", () => {
    render(<Sheet sheet={sheet} />);
    expect(screen.queryByRole("button", { name: "Ortofotomapa" })).not.toBeInTheDocument();
  });

  it("asks for nothing until the toggle is pressed", () => {
    const { container } = render(<Sheet sheet={sheet} photoUrl={photoUrl} />);
    expect(container.querySelector("image")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ortofotomapa" })).toHaveAttribute("aria-pressed", "false");
  });

  it("lays the photo under the drawing across the whole box, then credits the Geoportal", async () => {
    const user = userEvent.setup();
    const { container } = render(<Sheet sheet={sheet} photoUrl={photoUrl} />);
    await user.click(screen.getByRole("button", { name: "Ortofotomapa" }));
    const image = container.querySelector("image")!;
    expect(image.getAttribute("href")).toBe(photoUrl);
    const [x0, y0, x1, y1] = sheet.bbox_m as [number, number, number, number];
    expect([image.getAttribute("x"), image.getAttribute("y")]).toEqual([`${x0}`, `${-y1}`]);
    expect([image.getAttribute("width"), image.getAttribute("height")]).toEqual([`${x1 - x0}`, `${y1 - y0}`]);
    expect(screen.getByRole("status")).toHaveTextContent("Wczytuję ortofotomapę");
    // The photo is drawn first, so the parcel outline stays on top of it.
    const order = [...container.querySelectorAll("svg > g, svg > path")].map((el) => el.getAttribute("data-layer") ?? el.getAttribute("data-testid"));
    expect(order.indexOf("orthophoto")).toBeLessThan(order.indexOf("parcel"));
    fireEvent.load(image);
    expect(await screen.findByText(/Źródło ortofotomapy: Geoportal, GUGiK/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Ortofotomapa" }));
    expect(container.querySelector("image")).not.toBeInTheDocument();
  });

  it("says so when the photo cannot be fetched, and tries again on the next press", async () => {
    const user = userEvent.setup();
    const { container } = render(<Sheet sheet={sheet} photoUrl={photoUrl} />);
    await user.click(screen.getByRole("button", { name: "Ortofotomapa" }));
    fireEvent.error(container.querySelector("image")!);
    expect(await screen.findByRole("alert")).toHaveTextContent("Nie udało się pobrać ortofotomapy");
    expect(container.querySelector("image")).not.toBeInTheDocument(); // no broken-image icon on the drawing
    await user.click(screen.getByRole("button", { name: "Ortofotomapa" }));
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});

describe("Sheet roads", () => {
  it("draws a road as an open line: it is not closed back to where it started, and has no fill", () => {
    const { container } = render(<Sheet sheet={sheet} />);
    const road = container.querySelector('[data-layer="roads"]')!;
    expect(road.getAttribute("d")).toBe("M-50 -20L60 -25L120 -80");
    expect(road.getAttribute("fill")).toBe("none");
  });

  it("has a toggle like every other layer", async () => {
    const user = userEvent.setup();
    const { container } = render(<Sheet sheet={sheet} />);
    await user.click(screen.getByRole("button", { name: /Drogi publiczne/ }));
    expect(container.querySelector('[data-layer="roads"]')).not.toBeInTheDocument();
  });
});
