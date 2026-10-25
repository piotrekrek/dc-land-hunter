import { cleanup, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { NAV_ITEMS, pageTitle } from "@/components/layout/nav";
import { mockFetch, renderWithProviders } from "@/test/render";

import { AppRoutes } from "./App";

// MapLibre needs WebGL, which jsdom lacks.
vi.mock("@/components/map/MapView", () => ({ MapView: () => <div data-testid="map" /> }));

describe("App shell", () => {
  it("renders the Polish navigation and the home view", async () => {
    mockFetch({
      "/api/health": { status: "ok", version: "0.1.0", database: true, time: "" },
      "/api/auth/me": { email: "ala@example.org", role: "member" },
      "/api/sites": { total: 0, offset: 0, limit: 50, items: [] },
    });
    renderWithProviders(<AppRoutes />);
    await screen.findByText("Brak lokalizacji dla tych filtrów");
    const nav = screen.getAllByRole("navigation", { name: "Główna nawigacja" })[0]!;
    for (const item of NAV_ITEMS.filter((i) => !i.adminOnly && !i.iconOnly)) {
      expect(nav).toHaveTextContent(item.label);
    }
    // Nowości, Ustawienia and Zadania are icon-only links on the right, named by aria-label and title.
    for (const item of NAV_ITEMS.filter((i) => i.iconOnly && !i.adminOnly)) {
      expect(nav).not.toHaveTextContent(item.label);
      const name = item.soon ? `${item.label} (wkrótce)` : item.label;
      const link = within(screen.getByRole("navigation", { name: "Skróty" })).getByRole("link", { name });
      expect(link).toHaveAttribute("title", name);
      expect(link).toHaveAttribute("href", item.to);
    }
    expect(screen.queryByRole("link", { name: /Zadania/ })).not.toBeInTheDocument();
    expect(screen.getByText("Brak lokalizacji dla tych filtrów")).toBeInTheDocument();
    expect(screen.getByTestId("map")).toBeInTheDocument();
    expect(await screen.findByText("Połączono z API")).toBeInTheDocument();
  });

  it("tags the unfinished screens as wkrótce and only those", async () => {
    mockFetch({
      "/api/auth/me": { email: "ala@example.org", role: "member" },
      "/api/sites": { total: 0, offset: 0, limit: 50, items: [] },
    });
    renderWithProviders(<AppRoutes />);
    const nav = (await screen.findAllByRole("navigation", { name: "Główna nawigacja" }))[0]!;
    const tagged = within(nav).getAllByRole("link").filter((l) => l.textContent?.includes("wkrótce"));
    expect(tagged.map((l) => l.getAttribute("href"))).toEqual(["/projekty", "/eksport"]);
    expect(NAV_ITEMS.filter((i) => i.soon).map((i) => i.to)).toEqual(["/projekty", "/eksport", "/nowosci"]);
  });

  it("sets the tab title per route, including the not-found page", async () => {
    mockFetch({ "/api/auth/me": { email: "ala@example.org", role: "member" } });
    renderWithProviders(<AppRoutes />, { route: "/kolejka" });
    await waitFor(() => expect(document.title).toBe("Kolejka · DC Land Hunter"));
    cleanup();
    renderWithProviders(<AppRoutes />, { route: "/nie-ma-takiej-strony" });
    await waitFor(() => expect(document.title).toBe("Nie znaleziono strony · DC Land Hunter"));
  });

  it("puts Kolejka right after the map and keeps Źródła danych off the navbar", () => {
    expect(NAV_ITEMS.slice(0, 2).map((i) => i.label)).toEqual(["Mapa i lista", "Kolejka"]);
    expect(NAV_ITEMS.map((i) => i.label)).not.toContain("Źródła danych");
  });

  it("redirects the old /zrodla address to Ustawienia, which hosts the sources", async () => {
    mockFetch({
      "/api/auth/me": { email: "ala@example.org", role: "member" },
      "/api/sources": { database: true, sources: [] },
    });
    renderWithProviders(<AppRoutes />, { route: "/zrodla" });
    expect(await screen.findByRole("heading", { name: "Ustawienia" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Źródła danych" })).toBeInTheDocument();
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

describe("pageTitle", () => {
  it.each([
    ["/", "Mapa i lista · DC Land Hunter"],
    ["/kolejka", "Kolejka · DC Land Hunter"],
    ["/kolejka/", "Kolejka · DC Land Hunter"],
    ["/sprawdz-dzialke/abc", "Sprawdź działkę · DC Land Hunter"],
    ["/lokalizacje/12", "Karta lokalizacji · DC Land Hunter"],
    ["/logowanie", "Logowanie · DC Land Hunter"],
    ["/ustawienia", "Ustawienia · DC Land Hunter"],
    ["/nie-ma", "Nie znaleziono strony · DC Land Hunter"],
  ])("%s", (path, title) => {
    expect(pageTitle(path)).toBe(title);
  });
});
