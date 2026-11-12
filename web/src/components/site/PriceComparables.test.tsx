import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SitePrices } from "@/lib/prices";
import { renderWithProviders } from "@/test/render";

import { PriceComparables } from "./PriceComparables";

const sp = (s: string | null) => (s ?? "").replace(/\u00a0/g, " ");

const prices = (over: Partial<SitePrices> = {}, comp: Partial<SitePrices["comparables"]> = {}): SitePrices => ({
  comparables: {
    agricultural: { count: 5, median_zl_m2: 6, p25_zl_m2: 4, p75_zl_m2: 8, first_date: "2025-03-12", last_date: "2026-10-03" },
    other: { count: 2, median_zl_m2: 138.4, p25_zl_m2: 100, p75_zl_m2: 170, first_date: "2025-09-19", last_date: "2025-10-15" },
    widened: false,
    radius_km: 10,
    months: 36,
    source_id: "rcn",
    as_of: "2026-11-01T08:00:00+00:00",
    confidence: "verified",
    ...comp,
  },
  asking_zl_m2: null,
  asking_updated_at: null,
  asking_outlier: false,
  ...over,
});

/** Serves GET (and PUT, answering with `afterPut`) for /api/sites/site-a/prices or /asking-price. */
function serve(first: SitePrices, afterPut?: SitePrices) {
  const calls: { method: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const request = input as Request;
    const path = new URL(request.url, "http://localhost").pathname;
    const body = request.method === "PUT" ? await request.clone().json() : null;
    calls.push({ method: request.method, body });
    const ok = path === "/api/sites/site-a/prices" || path === "/api/sites/site-a/asking-price";
    const data = request.method === "PUT" ? (afterPut ?? first) : first;
    return ok
      ? new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } })
      : new Response("nope", { status: 404 });
  });
  return calls;
}

afterEach(() => vi.restoreAllMocks());

describe("PriceComparables", () => {
  it("shows both medians with the interquartile range, count, dates and the source", async () => {
    serve(prices());
    renderWithProviders(<PriceComparables siteId="site-a" />);
    const agri = await screen.findByTestId("price-group-Grunty rolne");
    expect(sp(within(agri).getByTestId("price-median").textContent)).toBe("6 zł/m²");
    expect(sp(agri.textContent)).toContain("typowo 4–8 zł/m²");
    expect(sp(agri.textContent)).toContain("5 transakcji, 12.03.2025–03.10.2026");
    const other = screen.getByTestId("price-group-Pozostałe grunty");
    expect(sp(within(other).getByTestId("price-median").textContent)).toBe("138 zł/m²");
    expect(screen.getByTestId("prices-source")).toHaveTextContent("RCN (GUGiK), import 01.11.2026");
    expect(screen.queryByTestId("prices-widened")).not.toBeInTheDocument();
    expect(screen.getByText("Zweryfikowane")).toBeInTheDocument();
  });

  it("says when the figures come from the whole powiat", async () => {
    serve(prices({}, { widened: true, confidence: "estimated" }));
    renderWithProviders(<PriceComparables siteId="site-a" />);
    expect(await screen.findByTestId("prices-widened")).toHaveTextContent(
      "Za mało transakcji w promieniu 10 km — dane z całego powiatu",
    );
    expect(screen.getByText("Szacunek")).toBeInTheDocument();
  });

  it("explains an empty result instead of showing zeros", async () => {
    const empty = { count: 0, median_zl_m2: null, p25_zl_m2: null, p75_zl_m2: null, first_date: null, last_date: null };
    serve(prices({}, { agricultural: empty, other: empty, confidence: "missing", as_of: null }));
    renderWithProviders(<PriceComparables siteId="site-a" />);
    expect(await screen.findByText(/Brak zaimportowanych transakcji/)).toBeInTheDocument();
    expect(screen.queryByTestId("price-median")).not.toBeInTheDocument();
  });

  it("warns when the asking price is more than 10 times the agricultural median", async () => {
    serve(prices({ asking_zl_m2: 80, asking_outlier: true }));
    renderWithProviders(<PriceComparables siteId="site-a" />);
    expect(await screen.findByTestId("asking-outlier")).toHaveTextContent(
      "Cena ofertowa ponad 10× wyższa od mediany dla gruntów rolnych",
    );
    expect(screen.getByLabelText(/Cena ofertowa/)).toHaveValue("80");
  });

  it("saves a typed asking price with a decimal comma", async () => {
    const calls = serve(prices(), prices({ asking_zl_m2: 12.5 }));
    renderWithProviders(<PriceComparables siteId="site-a" />);
    const input = await screen.findByLabelText(/Cena ofertowa/);
    const save = screen.getByRole("button", { name: "Zapisz" });
    expect(save).toBeDisabled();
    await userEvent.type(input, "12,5");
    await userEvent.click(save);
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ zl_m2: 12.5 });
    expect(await screen.findByLabelText(/Cena ofertowa/)).toHaveValue("12,5");
  });

  it("refuses text and zero before calling the server", async () => {
    const calls = serve(prices());
    renderWithProviders(<PriceComparables siteId="site-a" />);
    const input = await screen.findByLabelText(/Cena ofertowa/);
    await userEvent.type(input, "0");
    expect(screen.getByRole("alert")).toHaveTextContent("Wpisz liczbę większą od zera");
    expect(screen.getByRole("button", { name: "Zapisz" })).toBeDisabled();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("clears the asking price with Usuń by sending null", async () => {
    const calls = serve(prices({ asking_zl_m2: 20 }), prices());
    renderWithProviders(<PriceComparables siteId="site-a" />);
    await userEvent.click(await screen.findByRole("button", { name: "Usuń" }));
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ zl_m2: null });
  });

  it("shows a Polish error when the prices cannot be loaded", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("x", { status: 404 }));
    renderWithProviders(<PriceComparables siteId="site-a" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Nie udało się wczytać cen transakcyjnych.");
  });
});
