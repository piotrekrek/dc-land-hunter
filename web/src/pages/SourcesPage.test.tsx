import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { SourceOut } from "@/api/client";
import { formatDate, formatNumber } from "@/lib/format";
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

  it("shows notes, last error and row count on the table and the mobile cards when present", async () => {
    const withDetails = sources.map((s) =>
      s.id === "enea"
        ? {
            ...s,
            notes: "Tylko GPZ, bez mocy 110 kV",
            state: { last_success_at: "2026-10-18T08:00:00Z", last_error: "HTTP 503", row_count: 1234, stale: false },
          }
        : s,
    );
    mockFetch({ "/api/sources": { database: true, sources: withDetails }, "/api/auth/me": { email: "ala@example.org", role: "member" } });
    renderWithProviders(<SourcesPage />);
    const rows = await screen.findAllByTestId("source-row");
    const row = within(rows[1]!);
    expect(row.getByText("Tylko GPZ, bez mocy 110 kV")).toBeInTheDocument();
    expect(row.getByText("Ostatni błąd: HTTP 503")).toBeInTheDocument();
    expect(row.getByText(formatNumber(1234, 0))).toBeInTheDocument();

    const cards = screen.getAllByTestId("source-card");
    const card = within(cards[1]!);
    expect(card.getByText("Tylko GPZ, bez mocy 110 kV")).toBeInTheDocument();
    expect(card.getByText("Ostatni błąd: HTTP 503")).toBeInTheDocument();
    expect(card.getByText(formatNumber(1234, 0))).toBeInTheDocument();
    expect(card.getByText(formatDate("2026-10-18T08:00:00Z"))).toBeInTheDocument();
  });

  it("omits notes and error lines and shows dashes when the API sends none", async () => {
    mockFetch({ "/api/sources": { database: true, sources }, "/api/auth/me": { email: "ala@example.org", role: "member" } });
    renderWithProviders(<SourcesPage />);
    const rows = await screen.findAllByTestId("source-row");
    expect(within(rows[0]!).queryByText(/Ostatni błąd/)).not.toBeInTheDocument();
    expect(within(rows[0]!).getAllByText("—")).not.toHaveLength(0);
    const cards = screen.getAllByTestId("source-card");
    expect(within(cards[0]!).queryByText(/Ostatni błąd/)).not.toBeInTheDocument();
    expect(within(cards[0]!).getByText("Wiersze").nextElementSibling).toHaveTextContent("—");
    expect(within(cards[0]!).getByText("Ostatni sukces").nextElementSibling).toHaveTextContent("—");
  });

  it("exposes the summary grid as a named group", async () => {
    mockFetch({ "/api/sources": { database: true, sources }, "/api/auth/me": { email: "ala@example.org", role: "member" } });
    renderWithProviders(<SourcesPage />);
    await screen.findAllByTestId("source-row");
    expect(screen.getByRole("group", { name: "Podsumowanie źródeł MVP" })).toBeInTheDocument();
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
