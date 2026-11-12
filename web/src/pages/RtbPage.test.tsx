import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RtbExport } from "@/lib/rtb";
import rzepin from "@/test/rtbRzepin.json";
import { mockFetch, renderWithProviders } from "@/test/render";

import { RtbPage } from "./RtbPage";

const rtb = rzepin as unknown as RtbExport;
const PATH = "/api/sites/site-4755d239dc1f/rtb";

const renderRtb = () =>
  renderWithProviders(
    <Routes>
      <Route path="/lokalizacje/:siteId/rtb" element={<RtbPage />} />
    </Routes>,
    { route: "/lokalizacje/site-4755d239dc1f/rtb" },
  );

afterEach(() => vi.restoreAllMocks());

describe("RtbPage", () => {
  it("shows every part of the annex", async () => {
    mockFetch({ [PATH]: rtb });
    renderRtb();
    expect(await screen.findByTestId("rtb")).toBeInTheDocument();
    for (const title of [
      "Mapa: teren, linie 110 kV i stacje",
      "Czerwone flagi",
      "Obszary robocze: propozycja statusów",
      "Dowód mocy",
      "Koszt przyłączenia",
      "Ceny transakcyjne (RCN)",
      "Tabela faktów",
    ]) {
      expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
    }
    expect(screen.getByTestId("sheet")).toBeInTheDocument();
  });

  it("without evidence the power is yellow and the level reads Plotka", async () => {
    mockFetch({ [PATH]: rtb });
    renderRtb();
    const power = within(await screen.findByTestId("rag-power"));
    expect(power.getByText("Żółty")).toBeInTheDocument();
    expect(power.getByText(/Dowód mocy: Plotka/)).toBeInTheDocument();
    expect(within(screen.getByTestId("evidence-block")).getByText(/Plotka/)).toBeInTheDocument();
    expect(screen.getByTestId("evidence-block")).toHaveTextContent("domyślnie");
  });

  it("a deposit overlap makes the environment yellow and names the deposit and its source", async () => {
    mockFetch({ [PATH]: rtb });
    renderRtb();
    const env = within(await screen.findByTestId("rag-environment"));
    expect(env.getByText("Żółty")).toBeInTheDocument();
    expect(env.getByText(/Złoże „Rzepin \(węgle brunatne\)” pod 63% terenu/)).toBeInTheDocument();
    expect(env.getAllByText(/midas/).length).toBeGreaterThan(0);
  });

  it("lists the fired penalties as red flags with their source", async () => {
    mockFetch({ [PATH]: rtb });
    renderRtb();
    const flags = within(await screen.findByTestId("red-flags"));
    expect(flags.getByText("−8 pkt")).toBeInTheDocument();
    expect(flags.getByText("−10 pkt")).toBeInTheDocument();
    expect(flags.getAllByText(/midas/).length).toBeGreaterThan(0);
  });

  it("says there is no RCN data while the comparables are not filled", async () => {
    mockFetch({ [PATH]: rtb });
    renderRtb();
    expect(await screen.findByTestId("rcn-empty")).toHaveTextContent("Brak danych RCN");
  });

  it("shows the RCN medians and flags an asking price above ten times the agricultural median", async () => {
    const group = (median: number, count: number) => ({
      count,
      median_zl_m2: median,
      p25_zl_m2: median / 2,
      p75_zl_m2: median * 2,
      first_date: "2025-04-04",
      last_date: "2025-10-31",
      widened: true,
      confidence: "estimated" as const,
    });
    const price_comparables = {
      comparables: {
        agricultural: group(6, 5),
        other: group(51.5, 3),
        widened: true,
        radius_km: 10,
        months: 36,
        source_id: "rcn",
        as_of: "2026-11-12T10:00:00Z",
        confidence: "estimated" as const,
      },
      asking_zl_m2: 110,
      asking_updated_at: "2026-11-12T10:00:00Z",
      asking_outlier: true,
      asking_outlier_widened: true,
    };
    mockFetch({ [PATH]: { ...rtb, price_comparables } });
    renderRtb();
    const rcn = within(await screen.findByTestId("rcn-present"));
    expect(rcn.getAllByTestId("price-median")[0]).toHaveTextContent("6");
    expect(rcn.getByTestId("rcn-asking")).toHaveTextContent("ponad 10×");
    expect(rcn.getByTestId("rcn-asking")).toHaveTextContent("mediana z całego powiatu");
    expect(rcn.getByText(/import 12\.11\.2026/)).toBeInTheDocument();
  });

  it("marks each fact as verified, estimated or missing, each with a source", async () => {
    mockFetch({ [PATH]: rtb });
    renderRtb();
    const table = within(await screen.findByTestId("facts-table"));
    const rows = table.getAllByRole("row").slice(1);
    expect(rows.length).toBe(rtb.facts.length);
    const kinds = new Set(rows.map((r) => r.getAttribute("data-confidence")));
    expect(kinds).toEqual(new Set(["verified", "estimated", "missing"]));
    expect(table.getAllByRole("img", { name: "brak danych" }).length).toBeGreaterThan(0);
    expect(table.getByText("63,1 % terenu")).toBeInTheDocument();
  });

  it("shows the connection cost with the legal warning", async () => {
    mockFetch({ [PATH]: rtb });
    renderRtb();
    const cost = within(await screen.findByTestId("connection-cost"));
    expect(cost.getByText("Zaliczka na przyłączenie (zamrożona)")).toBeInTheDocument();
    expect(screen.getByText("wymaga weryfikacji prawnej")).toBeInTheDocument();
  });

  it("prints with the browser and downloads the JSON", async () => {
    mockFetch({ [PATH]: rtb });
    const print = vi.spyOn(window, "print").mockImplementation(() => undefined);
    const create = vi.fn(() => "blob:x");
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    renderRtb();
    await screen.findByTestId("rtb");
    await userEvent.click(screen.getByRole("button", { name: /Pobierz PDF/ }));
    expect(print).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole("button", { name: /Pobierz JSON/ }));
    expect(create).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
  });

  it("answers a missing site in Polish", async () => {
    mockFetch({});
    renderRtb();
    await waitFor(() => expect(screen.getByText(/Nie ma takiej lokalizacji/)).toBeInTheDocument());
  });
});
