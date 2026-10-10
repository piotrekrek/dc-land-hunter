import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { SourceOut } from "@/api/client";
import { mockFetch, renderWithProviders } from "@/test/render";

import { SourcesPage } from "./SourcesPage";

const base: Omit<SourceOut, "id" | "name" | "plan" | "discovery_status"> = {
  used_for: "test",
  cadence: "weekly",
  core: false,
  licence_expected: null,
  notes: null,
  manual_url: null,
  probe_count: 1,
  discovery_checked_at: null,
  state: null,
};

const sources: SourceOut[] = [
  { ...base, id: "egib", name: "EGiB", plan: "mvp", discovery_status: "verified", core: true },
  { ...base, id: "enea", name: "Enea Operator", plan: "mvp", discovery_status: "partial" },
  { ...base, id: "isok", name: "ISOK", plan: "mvp", discovery_status: "failed" },
  { ...base, id: "ekw", name: "Księgi wieczyste", plan: "manual", discovery_status: null, manual_url: "https://przegladarka-ekw.ms.gov.pl/" },
];

afterEach(() => vi.restoreAllMocks());

describe("SourcesPage", () => {
  it("shows each source with its Polish status label", async () => {
    mockFetch({ "/api/sources": { database: true, sources }, "/api/auth/me": { email: "ala@example.org", role: "member" } });
    renderWithProviders(<SourcesPage />);
    const rows = await screen.findAllByTestId("source-row");
    expect(rows).toHaveLength(4);
    expect(within(rows[0]!).getByText("Zweryfikowane")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("Do potwierdzenia")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("Niedostępne")).toBeInTheDocument();
    expect(within(rows[3]!).getByRole("link", { name: "Tylko link" })).toHaveAttribute("href", "https://przegladarka-ekw.ms.gov.pl/");
  });

  it("warns when the database is down", async () => {
    mockFetch({ "/api/sources": { database: false, sources }, "/api/auth/me": { email: "ala@example.org", role: "member" } });
    renderWithProviders(<SourcesPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Baza danych jest niedostępna");
  });

  it("tells the user to run discovery when nothing was checked", async () => {
    mockFetch({ "/api/sources": { database: true, sources: sources.map((s) => ({ ...s, discovery_status: null })) }, "/api/auth/me": { email: "ala@example.org", role: "member" } });
    renderWithProviders(<SourcesPage />);
    expect(await screen.findByText(/dclh discover/)).toBeInTheDocument();
  });

  it("shows 'Uruchom teraz' to admins only", async () => {
    mockFetch({ "/api/sources": { database: true, sources }, "/api/auth/me": { email: "ala@example.org", role: "member" } });
    const { unmount } = renderWithProviders(<SourcesPage />);
    await screen.findAllByTestId("source-row");
    expect(screen.queryByRole("button", { name: /Uruchom teraz/ })).not.toBeInTheDocument();
    unmount();
    vi.restoreAllMocks();
    mockFetch({ "/api/sources": { database: true, sources }, "/api/auth/me": { email: "root@example.org", role: "admin" } });
    renderWithProviders(<SourcesPage />);
    expect(await screen.findByRole("button", { name: /Uruchom teraz/ })).toBeInTheDocument();
  });

  it("shows an error state when the API fails", async () => {
    mockFetch({ "/api/auth/me": { email: "ala@example.org", role: "member" } });
    renderWithProviders(<SourcesPage />);
    expect(await screen.findByText("Nie udało się pobrać listy źródeł")).toBeInTheDocument();
  });
});
