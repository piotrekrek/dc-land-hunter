import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { NAV_ITEMS } from "@/components/layout/nav";
import { mockFetch, renderWithProviders } from "@/test/render";

import { AppRoutes } from "./App";

// MapLibre needs WebGL, which jsdom lacks.
vi.mock("@/components/map/MapView", () => ({ MapView: () => <div data-testid="map" /> }));

describe("App shell", () => {
  it("renders the Polish navigation and the home view", async () => {
    mockFetch({
      "/api/health": { status: "ok", version: "0.1.0", database: true, postgis_version: "3.4", time: "" },
      "/api/auth/me": { email: "ala@example.org", role: "member" },
      "/api/sites": { total: 0, offset: 0, limit: 50, items: [] },
    });
    renderWithProviders(<AppRoutes />);
    await screen.findByText("Brak lokalizacji dla tych filtrów");
    const nav = screen.getAllByRole("navigation", { name: "Główna nawigacja" })[0]!;
    for (const item of NAV_ITEMS) {
      expect(nav).toHaveTextContent(item.label);
    }
    expect(screen.getByText("Brak lokalizacji dla tych filtrów")).toBeInTheDocument();
    expect(screen.getByTestId("map")).toBeInTheDocument();
    expect(await screen.findByText("Połączono z API")).toBeInTheDocument();
  });

  it("routes to a placeholder screen", async () => {
    mockFetch({ "/api/auth/me": { email: "ala@example.org", role: "member" } });
    renderWithProviders(<AppRoutes />, { route: "/projekty" });
    expect(await screen.findByRole("heading", { name: "Moje projekty" })).toBeInTheDocument();
    expect(screen.getByText("Faza 4")).toBeInTheDocument();
  });

  it("shows a not-found page with a way back to the map for unknown addresses", async () => {
    mockFetch({ "/api/auth/me": { email: "ala@example.org", role: "member" } });
    renderWithProviders(<AppRoutes />, { route: "/nie-ma-takiej-strony" });
    expect(await screen.findByRole("heading", { name: "Nie znaleziono strony" })).toBeInTheDocument();
    expect(screen.queryByText(/Faza/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Wróć do: Mapa i lista" })).toHaveAttribute("href", "/");
  });

  it("redirects anonymous visitors to the login page", async () => {
    mockFetch({}); // /api/auth/me answers 404 -> treated as an error, so use 401 below
    vi.restoreAllMocks();
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 401, headers: { "Content-Type": "application/json" } }));
    renderWithProviders(<AppRoutes />, { route: "/ustawienia" });
    expect(await screen.findByRole("heading", { name: "Logowanie" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Główna nawigacja" })).not.toBeInTheDocument();
  });
});
