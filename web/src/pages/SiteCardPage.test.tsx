import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  gmina: "Brwinów",
  powiat: "powiat pruszkowski",
  voivodeship: "mazowieckie",
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
      key: "zoning",
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
  map: { origin_2180: [640000, 490000], bbox_m: [-100, -100, 100, 100], parcel: [[[-50, -40], [50, -40], [50, 40], [-50, 40]]] },
  watched: false,
};

afterEach(() => vi.restoreAllMocks());

describe("SiteCardPage", () => {
  it("shows the score, the Polish labels and the breakdown with provenance", async () => {
    mockFetch({ "/api/sites/site-a": detail });
    renderCard("/lokalizacje/site-a");
    expect(await screen.findByRole("heading", { name: /Lokalizacja: 32.ha/ })).toBeInTheDocument();
    expect(screen.getByText(/Szacowana moc 192 MW/)).toBeInTheDocument();
    expect(screen.getByText("Duży kampus: wymaga węzła 220/400 kV")).toBeInTheDocument();
    // The ruler replaces the round badge: the score, the potential and the thresholds are in one figure.
    expect(screen.getByRole("img", { name: "Linijka wyniku: 81, potencjał 84, progi 40 i 70" })).toBeInTheDocument();
    expect(screen.queryByTestId("gates-group")).not.toBeInTheDocument();

    const groups = screen.getAllByTestId("breakdown-group");
    expect(within(groups[0]!).getByRole("heading", { name: "Sieć energetyczna" })).toBeInTheDocument();
    expect(within(groups[0]!).getByText("+27 / 27")).toBeInTheDocument();
    expect(within(groups[0]!).getByText(/źródło: osm_power/)).toBeInTheDocument();
    expect(within(groups[1]!).getByText("Brak danych")).toBeInTheDocument();
  });

  it("shows both profile cards when the site has profiles, and none when it has not", async () => {
    const profiles = {
      built: { profile: "built", category: "yellow", score: 66, blockers: [], to_check: [], positives: [] },
      container: { profile: "container", category: "green", score: 81, blockers: [], to_check: [], positives: [] },
    };
    mockFetch({ "/api/sites/site-a": { ...detail, profiles } });
    renderCard("/lokalizacje/site-a");
    expect(await screen.findByTestId("profile-built")).toHaveTextContent("Warunkowo");
    expect(screen.getByTestId("profile-container")).toHaveTextContent("Warto");
  });

  it("shows no profile cards for a site stored before profiles existed", async () => {
    mockFetch({ "/api/sites/site-a": detail });
    renderCard("/lokalizacje/site-a");
    await screen.findByRole("heading", { name: /Lokalizacja: 32.ha/ });
    expect(screen.queryByTestId("profiles")).not.toBeInTheDocument();
  });

  it("links to a check of the same parcels with live layers (the id goes into the address)", async () => {
    mockFetch({ "/api/sites/site-a": detail });
    renderCard("/lokalizacje/site-a");
    const link = await screen.findByRole("link", { name: "Sprawdź te działki z aktualnymi danymi" });
    expect(link).toHaveAttribute("href", `/sprawdz-dzialke?q=${encodeURIComponent("1\n2")}`);
  });

  it("offers the check for a single parcel in the singular", async () => {
    mockFetch({ "/api/sites/site-a": { ...detail, parcel_ids: ["306201_1.0002.1573"] } });
    renderCard("/lokalizacje/site-a");
    expect(await screen.findByRole("link", { name: "Sprawdź tę działkę z aktualnymi danymi" })).toBeInTheDocument();
  });

  it("names the place in the header", async () => {
    mockFetch({ "/api/sites/site-a": detail });
    renderCard("/lokalizacje/site-a");
    expect(await screen.findByText("gm. Brwinów, pow. pruszkowski, woj. mazowieckie")).toBeInTheDocument();
  });

  it("lists the plot ids with a copy button and a Geoportal link each", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    mockFetch({ "/api/sites/site-a": { ...detail, parcel_ids: ["146501_8.0001.12/3", "146501_8.0001.14"] } });
    renderCard("/lokalizacje/site-a");
    const section = await screen.findByRole("region", { name: "Numery działek" });
    expect(within(section).getByRole("heading", { name: "Numery działek (2)" })).toBeInTheDocument();
    const links = within(section).getAllByRole("link", { name: /Geoportal/ });
    expect(links[0]).toHaveAttribute("href", `https://mapy.geoportal.gov.pl/imap/Imgp_2.html?identifyParcel=${encodeURIComponent("146501_8.0001.12/3")}`);
    await userEvent.click(within(section).getByRole("button", { name: "Kopiuj numer działki 146501_8.0001.14" }));
    expect(writeText).toHaveBeenCalledWith("146501_8.0001.14");
    await userEvent.click(within(section).getByRole("button", { name: "Kopiuj wszystkie numery działek" }));
    expect(writeText).toHaveBeenLastCalledWith("146501_8.0001.12/3\n146501_8.0001.14");
  });

  it("draws the site outline and offers the orthophoto from the site's own endpoint", async () => {
    mockFetch({ "/api/sites/site-a": detail });
    renderCard("/lokalizacje/site-a");
    const map = await screen.findByRole("region", { name: "Mapa lokalizacji" });
    expect(within(map).getByRole("img", { name: /Rysunek/ })).toBeInTheDocument();
    expect(within(map).getByRole("button", { name: "Ortofotomapa" })).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(within(map).getByRole("button", { name: "Ortofotomapa" }));
    expect(map.querySelector("image")?.getAttribute("href")).toBe("/api/sites/site-a/orthophoto");
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
    const gates = screen.getByTestId("gates-group");
    expect(within(gates).getByRole("heading", { name: "Warunki konieczne" })).toBeInTheDocument();
    expect(within(gates).getByText("niespełnione: 1")).toBeInTheDocument();
    expect(within(gates).getByRole("img", { name: "nie spełnia warunku" })).toBeInTheDocument();
    expect(within(gates).getByText("−30 pkt")).toBeInTheDocument();
    expect(screen.getByTestId("ruler")).toBeInTheDocument();
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

  it("puts the missing items first in Do sprawdzenia, with who to ask", async () => {
    mockFetch({ "/api/sites/site-a": detail });
    renderCard("/lokalizacje/site-a");
    const block = await screen.findByTestId("to-check");
    expect(within(block).getByRole("heading", { name: "Do sprawdzenia (1)" })).toBeInTheDocument();
    expect(within(block).getByText("Plan miejscowy")).toBeInTheDocument();
    expect(within(block).getByText("do +5 pkt")).toBeInTheDocument();
    expect(within(block).getByText(/Gmina \(wydział planowania/)).toBeInTheDocument();
  });

  it("hides Do sprawdzenia when every item has data", async () => {
    mockFetch({ "/api/sites/site-a": { ...detail, items: detail.items.map((i) => ({ ...i, confidence: "estimated" as const })) } });
    renderCard("/lokalizacje/site-a");
    await screen.findByRole("heading", { name: /Lokalizacja/ });
    expect(screen.queryByTestId("to-check")).not.toBeInTheDocument();
  });

  it("calculates the connection cost from the estimate and lets the user change the power", async () => {
    mockFetch({ "/api/sites/site-a": { ...detail, estimated_mw: 60 } });
    renderCard("/lokalizacje/site-a");
    const calc = await screen.findByTestId("cost-calculator");
    expect(within(calc).getByText("wymaga weryfikacji prawnej")).toBeInTheDocument();
    expect(within(calc).getByTestId("frozen-total")).toHaveTextContent(/5.400.000 zł/);
    const input = within(calc).getByRole("textbox", { name: /Moc przyłączeniowa/ });
    await userEvent.clear(input);
    await userEvent.type(input, "10");
    expect(within(calc).getByTestId("frozen-total")).toHaveTextContent(/900.000 zł/);
    expect(within(calc).getByText(/spółce celowej \(SPV\)/)).toBeInTheDocument();
  });

  it("watches and unwatches, and moves the site through the stages", async () => {
    let state: { watched: boolean; stage: string | null; project_note: string | null } = {
      watched: false,
      stage: null,
      project_note: null,
    };
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const req = input instanceof Request ? input : new Request(input, init);
      const path = new URL(req.url, "http://localhost").pathname;
      calls.push(`${req.method} ${path}`);
      if (path === "/api/sites/site-a/watch") state = { ...state, watched: req.method === "PUT" };
      if (path === "/api/sites/site-a/project") {
        if (req.method === "PUT") state = { ...state, stage: ((await req.json()) as { stage: string }).stage };
        else state = { ...state, stage: null, project_note: null };
        if (req.method === "DELETE") return new Response(null, { status: 204 });
      }
      const body = path === "/api/sites/site-a" ? { ...detail, ...state } : state;
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    renderCard("/lokalizacje/site-a");
    const watch = await screen.findByRole("button", { name: "Obserwuj" });
    expect(watch).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(watch);
    expect(await screen.findByRole("button", { name: "Przestań obserwować" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Przestań obserwować" }));
    expect(await screen.findByRole("button", { name: "Obserwuj" })).toBeInTheDocument();

    expect(screen.queryByRole("combobox", { name: "Etap projektu" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Dodaj do projektu" }));
    const select = await screen.findByRole("combobox", { name: "Etap projektu" });
    expect(select).toHaveValue("new");
    expect(screen.getByText(/dane osobowe \(RODO\)/)).toBeInTheDocument();
    await userEvent.selectOptions(select, "Negocjacje");
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Etap projektu" })).toHaveValue("negotiation"));
    await userEvent.click(screen.getByRole("button", { name: /Usuń z projektów/ }));
    expect(await screen.findByRole("button", { name: "Dodaj do projektu" })).toBeInTheDocument();
    expect(calls).toContain("DELETE /api/sites/site-a/project");
  });
});
