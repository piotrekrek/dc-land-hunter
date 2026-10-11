import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { components } from "@/api/schema";
import { mockFetch, renderWithProviders } from "@/test/render";

import { SiteList } from "./SiteList";

type SiteSummary = components["schemas"]["SiteSummary"];

const estimated_mw_default = 192;

const site = (over: Partial<SiteSummary>): SiteSummary => ({
  site_id: "site-a",
  zone_id: "z-a",
  area_ha: 32,
  estimated_mw: 192,
  score: 81.4,
  upside_score: 84,
  completeness: 0.75,
  excluded: false,
  grid_badge: "Opublikowana moc",
  large_campus: false,
  split_suggested: false,
  flags: [],
  lon: 19.9,
  lat: 52.1,
  mw_fact: { value: estimated_mw_default, source_id: "egib", as_of: null, confidence: "estimated" },
  ...over,
});

afterEach(() => vi.restoreAllMocks());

describe("SiteList", () => {
  it("shows the ranked sites with Polish units and the grid badge", async () => {
    mockFetch({
      "/api/sites": {
        total: 2,
        offset: 0,
        limit: 50,
        items: [site({}), site({ site_id: "site-b", score: 42, area_ha: 12.5, estimated_mw: 75, grid_badge: "Bliskość sieci" })],
      },
    });
    renderWithProviders(<SiteList />);
    const rows = await screen.findAllByTestId("site-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByText(/32 ha/)).toBeInTheDocument();
    expect(within(rows[0]!).getByText("Opublikowana moc")).toBeInTheDocument();
    expect(within(rows[1]!).getByText(/12,5 ha/)).toBeInTheDocument();
    expect(within(rows[1]!).getByText(/75 MW/)).toBeInTheDocument();
    expect(within(rows[0]!).getByLabelText("Wynik 81 na 100")).toBeInTheDocument();
  });

  it("explains the empty state when the filters hide everything", async () => {
    mockFetch({ "/api/sites": { total: 0, offset: 0, limit: 50, items: [] } });
    renderWithProviders(<SiteList />);
    expect(await screen.findByText("Brak lokalizacji dla tych filtrów")).toBeInTheDocument();
  });

  it("offers more results when the page is not complete, and asks for the next offset", async () => {
    const fetchSpy = mockFetch({ "/api/sites": { total: 80, offset: 0, limit: 50, items: [site({})] } });
    renderWithProviders(<SiteList />);
    const more = await screen.findByRole("button", { name: /Pokaż więcej \(1 z 80\)/ });
    await userEvent.click(more);
    const urls = fetchSpy.mock.calls.map(([input]) => (typeof input === "string" ? input : (input as Request).url));
    expect(urls.some((u) => u.includes("offset=1"))).toBe(true);
  });

  it("keeps the ranked list in a scrollable region", async () => {
    mockFetch({ "/api/sites": { total: 1, offset: 0, limit: 50, items: [site({})] } });
    renderWithProviders(<SiteList />);
    await screen.findAllByTestId("site-row");
    expect(screen.getByRole("region", { name: "Wyniki" })).toContainElement(screen.getByRole("list", { name: "Ranking lokalizacji" }));
  });

  it("marks excluded sites and shows their upside", async () => {
    mockFetch({
      "/api/sites": {
        total: 1,
        offset: 0,
        limit: 50,
        items: [site({ excluded: true, score: 0, upside_score: 30, completeness: 0.4 })],
      },
    });
    renderWithProviders(<SiteList />, { route: "/?excluded=1" });
    expect(await screen.findByText("Wykluczona")).toBeInTheDocument();
    expect(screen.getByText(/po wyjaśnieniu brakujących danych do 30 pkt/)).toBeInTheDocument();
  });
});
