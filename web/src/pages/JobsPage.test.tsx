import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { OpsJobs } from "@/api/client";
import { AppShell } from "@/components/layout/AppShell";
import { mockFetch, renderWithProviders } from "@/test/render";

import { JobsPage } from "./JobsPage";

const ops: OpsJobs = {
  counts: { pending: 1, running: 0, done: 5, failed: 2 },
  failing_powiats: [
    { teryt: "0202", name: "powiat dzierżoniowski", failed_at: "2026-10-20T23:01:00Z", error: "IntegrityError: duplicate key" },
  ],
  attention: 3,
  recent: [
    {
      id: 61,
      kind: "search",
      status: "failed",
      powiat: "0202",
      powiat_name: "powiat dzierżoniowski",
      requested_by: "planner",
      created_at: "2026-10-20T23:00:03Z",
      finished_at: "2026-10-20T23:01:00Z",
      error: "IntegrityError: duplicate key",
    },
    {
      id: 60,
      kind: "discover",
      status: "done",
      powiat: null,
      powiat_name: null,
      requested_by: "ala@example.org",
      created_at: "2026-10-20T20:00:00Z",
      finished_at: "2026-10-20T20:01:00Z",
      error: null,
    },
  ],
};

const admin = { email: "ala@example.org", role: "admin" };
const member = { email: "ala@example.org", role: "member" };

afterEach(() => vi.restoreAllMocks());

describe("JobsPage", () => {
  it("shows the failing powiat, the counts and the recent jobs in Polish", async () => {
    mockFetch({ "/api/ops/jobs": ops, "/api/auth/me": admin });
    renderWithProviders(<JobsPage />);
    const failing = await screen.findByTestId("failing-powiat");
    expect(within(failing).getByText(/powiat dzierżoniowski/)).toBeInTheDocument();
    expect(within(failing).getByText("IntegrityError: duplicate key")).toBeInTheDocument();
    const rows = screen.getAllByTestId("job-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByText("Błąd")).toBeInTheDocument();
    expect(within(rows[0]!).getByText("Wyszukiwanie")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("Zakończone")).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Podsumowanie zadań" })).getByText("2")).toBeInTheDocument();
  });

  it("says so when every powiat's latest search succeeded", async () => {
    mockFetch({ "/api/ops/jobs": { ...ops, failing_powiats: [], attention: 0 }, "/api/auth/me": admin });
    renderWithProviders(<JobsPage />);
    expect(await screen.findByText(/zakończyło się sukcesem/)).toBeInTheDocument();
    expect(screen.queryByTestId("failing-powiat")).not.toBeInTheDocument();
  });

  it("refuses a member without asking the API for job data", async () => {
    const fetchMock = mockFetch({ "/api/ops/jobs": ops, "/api/auth/me": member });
    renderWithProviders(<JobsPage />);
    expect(await screen.findByText("Brak uprawnień")).toBeInTheDocument();
    const paths = fetchMock.mock.calls.map(([input]) => new URL(String(input instanceof Request ? input.url : input), "http://localhost").pathname);
    expect(paths).not.toContain("/api/ops/jobs");
  });
});

describe("navigation alert", () => {
  it("shows the attention count to an admin", async () => {
    mockFetch({ "/api/ops/jobs": ops, "/api/auth/me": admin });
    renderWithProviders(<AppShell />);
    expect((await screen.findAllByTestId("jobs-attention"))[0]).toHaveTextContent("3");
    expect(screen.getAllByRole("link", { name: /Zadania/ }).length).toBeGreaterThan(0);
  });

  it("hides the entry and the badge from a member", async () => {
    mockFetch({ "/api/ops/jobs": ops, "/api/auth/me": member });
    renderWithProviders(<AppShell />);
    await screen.findByText("Mapa i lista");
    expect(screen.queryByRole("link", { name: /Zadania/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId("jobs-attention")).not.toBeInTheDocument();
  });
});
