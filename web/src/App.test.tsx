import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { NAV_ITEMS } from "@/components/layout/nav";
import { mockFetch, renderWithProviders } from "@/test/render";

import { AppRoutes } from "./App";

// MapLibre needs WebGL, which jsdom lacks.
vi.mock("@/components/map/MapView", () => ({ MapView: () => <div data-testid="map" /> }));

describe("App shell", () => {
  it("renders the Polish navigation and the home view", async () => {
    mockFetch({ "/api/health": { status: "ok", version: "0.1.0", database: true, postgis_version: "3.4", time: "" } });
    renderWithProviders(<AppRoutes />);
    const nav = screen.getAllByRole("navigation", { name: "Główna nawigacja" })[0]!;
    for (const item of NAV_ITEMS) {
      expect(nav).toHaveTextContent(item.label);
    }
    expect(screen.getByText("Brak lokalizacji do wyświetlenia")).toBeInTheDocument();
    expect(screen.getByTestId("map")).toBeInTheDocument();
    expect(await screen.findByText("Połączono z API")).toBeInTheDocument();
  });

  it("routes to a placeholder screen", () => {
    mockFetch({});
    renderWithProviders(<AppRoutes />, { route: "/projekty" });
    expect(screen.getByRole("heading", { name: "Moje projekty" })).toBeInTheDocument();
    expect(screen.getByText("Faza 4")).toBeInTheDocument();
  });
});
