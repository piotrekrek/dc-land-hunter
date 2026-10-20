import { screen, within } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { components } from "@/api/schema";
import { mockFetch, renderWithProviders } from "@/test/render";

import { SiteCardPage } from "./SiteCardPage";

const renderCard = (route: string) =>
  renderWithProviders(
    <Routes>
      <Route path="/lokalizacje/:siteId" element={<SiteCardPage />} />
    </Routes>,
    { route },
  );

type SiteDetail = components["schemas"]["SiteDetail"];

const detail: SiteDetail = {
  site_id: "site-a",
  zone_id: "z-a",
  area_ha: 32,
  estimated_mw: 192,
  score: 81.4,
  upside_score: 84,
  completeness: 0.75,
  excluded: false,
  grid_badge: "Opublikowana moc",
  large_campus: true,
  split_suggested: false,
  flags: ["Duży kampus"],
  lon: 19.9,
  lat: 52.1,
  mw_fact: { value: 192, source_id: "egib", as_of: "2026-10-10T06:00:00+00:00", confidence: "estimated" },
  parcel_ids: ["1", "2"],
  scored_at: "2026-10-10T06:00:00+00:00",
  exclusions: [],
  categories: [
    { category: "grid", points: 40, max_points: 45, upside_points: 40 },
    { category: "zoning", points: 20, max_points: 25, upside_points: 25 },
  ],
  items: [
    {
      key: "line110",
      category: "grid",
      label_pl: "Linia 110 kV",
      points: 27,
      max_points: 27,
      upside_points: 27,
      confidence: "estimated",
      source_id: "osm_power",
      as_of: null,
      explanation_pl: "Linia 110 kV w odległości 0,6 km.",
      value: 0.6,
    },
    {
      key: "plan",
      category: "zoning",
      label_pl: "Plan miejscowy",
      points: 20,
      max_points: 25,
      upside_points: 25,
      confidence: "missing",
      source_id: "ru",
      as_of: null,
      explanation_pl: "Brak planu w rejestrze.",
      value: null,
    },
  ],
  features: {},
  geometry: { type: "Polygon", coordinates: [] },
};

afterEach(() => vi.restoreAllMocks());

describe("SiteCardPage", () => {
  it("shows the score, the Polish labels and the breakdown with provenance", async () => {
    mockFetch({ "/api/sites/site-a": detail });
    renderCard("/lokalizacje/site-a");
    expect(await screen.findByRole("heading", { name: /Lokalizacja: 32.ha/ })).toBeInTheDocument();
    expect(screen.getByText(/Szacowana moc 192 MW/)).toBeInTheDocument();
    expect(screen.getByText("Duży kampus: wymaga węzła 220/400 kV")).toBeInTheDocument();

    const groups = screen.getAllByTestId("breakdown-group");
    expect(within(groups[0]!).getByRole("heading", { name: "Sieć energetyczna" })).toBeInTheDocument();
    expect(within(groups[0]!).getByText("+27 / 27")).toBeInTheDocument();
    expect(within(groups[0]!).getByText(/źródło: osm_power/)).toBeInTheDocument();
    expect(within(groups[1]!).getByText("Brak danych")).toBeInTheDocument();
  });

  it("lists exclusion reasons for an excluded site", async () => {
    mockFetch({
      "/api/sites/site-a": {
        ...detail,
        excluded: true,
        exclusions: [{ key: "area_below_min", reason_pl: "Powierzchnia poniżej 10 ha.", mode: "exclude", penalty_points: 30 }],
      },
    });
    renderCard("/lokalizacje/site-a");
    expect(await screen.findByText("Powierzchnia poniżej 10 ha.")).toBeInTheDocument();
    expect(screen.getByText("Lokalizacja nie spełnia warunków minimalnych")).toBeInTheDocument();
  });

  it("says so when the site is not in the list any more", async () => {
    mockFetch({});
    renderCard("/lokalizacje/site-gone");
    expect(await screen.findByText("Nie ma takiej lokalizacji")).toBeInTheDocument();
  });

  it("does not retry a 401: the expired session is reported at once", async () => {
    const siteCalls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/api/auth/me") {
        return new Response(JSON.stringify({ email: "ala@example.org", role: "member" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      siteCalls.push(path);
      return new Response(JSON.stringify({ detail: "Wymagane logowanie" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      });
    });
    renderCard("/lokalizacje/site-a");
    // A retry would keep the card in "Wczytuję kartę…" for a second or more before this alert appears.
    expect(await screen.findByRole("alert")).toHaveTextContent("Nie udało się wczytać karty lokalizacji.");
    expect(screen.queryByText("Wczytuję kartę…")).not.toBeInTheDocument();
    expect(siteCalls).toEqual(["/api/sites/site-a"]);
  });
});
