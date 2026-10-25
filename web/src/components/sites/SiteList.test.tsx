import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
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
  gmina: null,
  powiat: null,
  voivodeship: null,
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
    renderWithProviders(<SiteList onSelect={() => {}} />);
    const rows = await screen.findAllByTestId("site-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByText(/32 ha/)).toBeInTheDocument();
    expect(within(rows[0]!).getByText("Opublikowana moc")).toBeInTheDocument();
    expect(within(rows[1]!).getByText(/12,5 ha/)).toBeInTheDocument();
    expect(within(rows[1]!).getByText(/75 MW/)).toBeInTheDocument();
    expect(within(rows[0]!).getByLabelText("Wynik 81 na 100")).toBeInTheDocument();
  });

  it("selects a site on click and links to its page with Więcej informacji", async () => {
    mockFetch({ "/api/sites": { total: 2, offset: 0, limit: 50, items: [site({}), site({ site_id: "site-b", lon: 20.5, lat: 51.2 })] } });
    const onSelect = vi.fn();
    renderWithProviders(<SiteList selectedId="site-b" onSelect={onSelect} />);
    const rows = await screen.findAllByTestId("site-row");

    await userEvent.click(within(rows[1]!).getByRole("button", { name: /Kompletność danych/ }));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0]![0]).toMatchObject({ site_id: "site-b", lon: 20.5, lat: 51.2 });

    expect(within(rows[1]!).getByRole("button")).toHaveAttribute("aria-pressed", "true");
    expect(within(rows[0]!).getByRole("button")).toHaveAttribute("aria-pressed", "false");
    expect(within(rows[0]!).getByRole("link", { name: "Więcej informacji" })).toHaveAttribute("href", "/lokalizacje/site-a");
  });

  it("links each site to its location in Google Maps without selecting it", async () => {
    mockFetch({ "/api/sites": { total: 1, offset: 0, limit: 50, items: [site({ lon: 20.5, lat: 51.2 })] } });
    const onSelect = vi.fn();
    renderWithProviders(<SiteList onSelect={onSelect} />);
    const link = await screen.findByRole("link", { name: "Mapy Google" });
    expect(link).toHaveAttribute("href", "https://www.google.com/maps?q=51.200000,20.500000");
    expect(link).toHaveAttribute("target", "_blank");
    await userEvent.click(link);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("does not select the site when the Więcej informacji link is used", async () => {
    mockFetch({ "/api/sites": { total: 1, offset: 0, limit: 50, items: [site({})] } });
    const onSelect = vi.fn();
    renderWithProviders(<SiteList onSelect={onSelect} />);
    await userEvent.click(await screen.findByRole("link", { name: "Więcej informacji" }));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("explains the empty state when the filters hide everything", async () => {
    mockFetch({ "/api/sites": { total: 0, offset: 0, limit: 50, items: [] } });
    renderWithProviders(<SiteList onSelect={() => {}} />);
    expect(await screen.findByText("Brak lokalizacji dla tych filtrów")).toBeInTheDocument();
    expect(screen.getByText(/Poszerz filtry albo pokaż wykluczone lokalizacje/)).toBeInTheDocument();
    expect(screen.queryByText(/Faza/)).not.toBeInTheDocument();
  });

  it("states once, in the list header, that the power is an area-based estimate", async () => {
    mockFetch({ "/api/sites": { total: 2, offset: 0, limit: 50, items: [site({}), site({ site_id: "site-b" })] } });
    renderWithProviders(<SiteList onSelect={() => {}} />);
    const rows = await screen.findAllByTestId("site-row");
    expect(screen.getAllByText(/szacunek na podstawie powierzchni/)).toHaveLength(1);
    expect(screen.getByText(/Moc to szacunek na podstawie powierzchni/)).toBeInTheDocument();
    for (const row of rows) expect(within(row).queryByText(/szacunek/)).not.toBeInTheDocument();
  });

  it("gives the row links a 44px-high tap target without adding to the row height", async () => {
    mockFetch({ "/api/sites": { total: 1, offset: 0, limit: 50, items: [site({})] } });
    renderWithProviders(<SiteList onSelect={() => {}} />);
    const details = await screen.findByRole("link", { name: "Więcej informacji" });
    const maps = screen.getByRole("link", { name: "Mapy Google" });
    // text-sm line (20px) + py-3 (12px top and bottom) = 44px; -my-3 cancels the padding in the layout.
    for (const link of [details, maps]) {
      expect(link.className).toMatch(/\bpy-3\b/);
      expect(link.className).toMatch(/(^|\s)-my-3(\s|$)/);
    }
  });

  it("offers more results when the page is not complete, and asks for the next page with its cursor", async () => {
    // The second page must hold a different site; the same id twice would render duplicate React keys.
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const cursor = new URL(url, "http://localhost").searchParams.get("cursor");
      const page =
        cursor === null
          ? { items: [site({ site_id: "site-a" })], next_cursor: "cursor-2" }
          : { items: [site({ site_id: "site-b" })], next_cursor: null };
      return new Response(JSON.stringify({ total: 2, offset: 0, limit: 50, ...page }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    renderWithProviders(<SiteList onSelect={() => {}} />);
    const more = await screen.findByRole("button", { name: /Pokaż więcej \(1 z 2\)/ });
    await userEvent.click(more);
    expect(await screen.findAllByTestId("site-row")).toHaveLength(2);
    const urls = fetchSpy.mock.calls.map(([input]) => (typeof input === "string" ? input : (input as Request).url));
    expect(urls.some((u) => u.includes("cursor=cursor-2"))).toBe(true);
    expect(urls.some((u) => u.includes("offset="))).toBe(false);
  });

  it("shows a site once when two pages both list it", async () => {
    // A scoring run between the two requests can move a site across the page boundary.
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const cursor = new URL(url, "http://localhost").searchParams.get("cursor");
      const page =
        cursor === null
          ? { items: [site({ site_id: "site-a" }), site({ site_id: "site-b" })], next_cursor: "cursor-2" }
          : { items: [site({ site_id: "site-b" }), site({ site_id: "site-c" })], next_cursor: null };
      return new Response(JSON.stringify({ total: 3, offset: 0, limit: 50, ...page }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    renderWithProviders(<SiteList onSelect={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: /Pokaż więcej \(2 z 3\)/ }));
    expect(await screen.findAllByTestId("site-row")).toHaveLength(3);
  });

  it("names the active filters and resets them to the defaults", async () => {
    mockFetch({ "/api/sites": { total: 1, offset: 0, limit: 50, items: [site({})] } });
    renderWithProviders(<SiteList onSelect={() => {}} />, { route: "/?minMw=100&minHa=20" });
    expect(await screen.findByText("Filtry: od 100 MW, powierzchnia od 20 ha")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Wyczyść filtry" }));
    expect(screen.getByText("Filtry domyślne: od 60 MW")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Wyczyść filtry" })).toBeDisabled();
  });

  it("offers one minimum-power filter and no separate area filter", async () => {
    mockFetch({ "/api/sites": { total: 1, offset: 0, limit: 50, items: [site({})] } });
    renderWithProviders(<SiteList onSelect={() => {}} />);
    await screen.findAllByTestId("site-row");
    expect(screen.getByLabelText("Szacowana moc od")).toHaveValue("60");
    expect(screen.queryByLabelText("Powierzchnia od")).not.toBeInTheDocument();
    expect(screen.getByText("Filtry domyślne: od 60 MW")).toBeInTheDocument();
  });

  it("keeps the ranked list in a scrollable region", async () => {
    mockFetch({ "/api/sites": { total: 1, offset: 0, limit: 50, items: [site({})] } });
    renderWithProviders(<SiteList onSelect={() => {}} />);
    await screen.findAllByTestId("site-row");
    expect(screen.getByRole("region", { name: "Wyniki" })).toContainElement(screen.getByRole("list", { name: "Ranking lokalizacji" }));
  });

  it("scrolls the selected row into view, so a site picked on the map shows in the list", async () => {
    // jsdom has no layout, so record which row each scroll was asked for instead of checking the position.
    const scrolled: Element[] = [];
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) {
      scrolled.push(this);
    };
    try {
      mockFetch({ "/api/sites": { total: 2, offset: 0, limit: 50, items: [site({}), site({ site_id: "site-b" })] } });
      // Stands in for the map: picking a site there changes the selected id the list receives.
      const WithMapPick = () => {
        const [selectedId, setSelectedId] = useState<string | null>(null);
        return (
          <>
            <button onClick={() => setSelectedId("site-b")}>Wybierz na mapie</button>
            <SiteList selectedId={selectedId} onSelect={() => {}} />
          </>
        );
      };
      renderWithProviders(<WithMapPick />);
      const rows = await screen.findAllByTestId("site-row");
      expect(scrolled).toHaveLength(0);

      await userEvent.click(screen.getByRole("button", { name: "Wybierz na mapie" }));
      expect(scrolled).toEqual([rows[1]]);
    } finally {
      Element.prototype.scrollIntoView = original;
    }
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
    renderWithProviders(<SiteList onSelect={() => {}} />, { route: "/?excluded=1" });
    expect(await screen.findByText("Wykluczona")).toBeInTheDocument();
    expect(screen.getByText(/po wyjaśnieniu brakujących danych do 30 pkt/)).toBeInTheDocument();
  });

  it("says where each site is under the headline, and leaves the line out when no place is known", async () => {
    mockFetch({
      "/api/sites": {
        total: 2,
        offset: 0,
        limit: 50,
        items: [
          site({ gmina: "Brwinów", powiat: "powiat pruszkowski", voivodeship: "mazowieckie" }),
          site({ site_id: "site-b" }),
        ],
      },
    });
    renderWithProviders(<SiteList onSelect={() => {}} />);
    const rows = await screen.findAllByTestId("site-row");
    expect(within(rows[0]!).getByText("gm. Brwinów, pow. pruszkowski")).toBeInTheDocument();
    expect(within(rows[1]!).queryByText(/gm\.|pow\./)).not.toBeInTheDocument();
  });

  it("filters by powiat: the choice goes to the API and the address, and the list is asked again", async () => {
    const area = (teryt: string, name: string) => ({ teryt, name, sites: 3, with_data: 1, total: 4, share: 0.25, by_item: {} });
    const asked: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, "http://localhost");
      asked.push(url.pathname + url.search);
      const body =
        url.pathname === "/api/coverage"
          ? {
              level: url.searchParams.get("level"),
              item: "all",
              areas:
                url.searchParams.get("level") === "powiat"
                  ? [area("1421", "powiat pruszkowski"), area("2401", "powiat będziński")]
                  : [area("14", "mazowieckie"), area("24", "śląskie")],
            }
          : { total: 1, offset: 0, limit: 50, items: [site({})] };
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    renderWithProviders(<SiteList onSelect={() => {}} />);
    await screen.findAllByTestId("site-row");
    await screen.findByRole("option", { name: "powiat pruszkowski" });

    await userEvent.selectOptions(screen.getByLabelText("Powiat"), "1421");
    await waitFor(() => expect(asked.some((u) => u.startsWith("/api/sites") && u.includes("powiat=1421"))).toBe(true));
    expect(screen.getByLabelText("Powiat")).toHaveValue("1421");

    // Another voivodeship leaves the powiat outside it: the powiat is cleared and its list narrowed.
    await userEvent.selectOptions(screen.getByLabelText("Województwo"), "24");
    expect(screen.getByLabelText("Powiat")).toHaveValue("");
    expect(screen.queryByRole("option", { name: "powiat pruszkowski" })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "powiat będziński" })).toBeInTheDocument();
  });
});
