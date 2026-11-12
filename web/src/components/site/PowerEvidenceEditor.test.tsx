import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/render";

import { PowerEvidenceEditor } from "./PowerEvidenceEditor";

afterEach(() => vi.restoreAllMocks());

/** The requests that change something (the auth provider also asks who is signed in). */
const puts = (spy: { mock: { calls: unknown[][] } }) =>
  spy.mock.calls.map((c) => c[0] as Request).filter((r) => r.method === "PUT");

const none = { level: "rumour", recorded: false } as const;

describe("PowerEvidenceEditor", () => {
  it("offers the five levels in Polish and starts at Plotka", () => {
    renderWithProviders(<PowerEvidenceEditor siteId="s1" evidence={none} />);
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual([
      "Plotka",
      "Nieformalna opinia operatora",
      "Wniosek złożony (opłata i zaliczka)",
      "Warunki wydane",
      "Umowa podpisana",
    ]);
    expect(screen.getByRole("combobox")).toHaveValue("rumour");
    expect(screen.getByText("Plotka (nic nie wpisano)")).toBeInTheDocument();
  });

  it("asks for an expiry only once conditions are issued", async () => {
    renderWithProviders(<PowerEvidenceEditor siteId="s1" evidence={none} />);
    expect(screen.queryByLabelText("Warunki ważne do")).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox"), "conditions_issued");
    expect(screen.getByLabelText("Warunki ważne do")).toBeInTheDocument();
  });

  it("saves the level, dates and link", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
      async () =>
        new Response(JSON.stringify({ level: "conditions_issued", recorded: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    renderWithProviders(<PowerEvidenceEditor siteId="s1" evidence={none} />);
    await userEvent.selectOptions(screen.getByRole("combobox"), "conditions_issued");
    await userEvent.type(screen.getByLabelText("Data dokumentu"), "2026-10-01");
    await userEvent.type(screen.getByLabelText("Warunki ważne do"), "2027-10-01");
    await userEvent.type(screen.getByLabelText("Link do dokumentu"), "https://drive.example.org/f/1");
    await userEvent.click(screen.getByRole("button", { name: "Zapisz dowód mocy" }));
    await waitFor(() => expect(puts(fetchMock)).toHaveLength(1));
    const request = puts(fetchMock)[0]!;
    expect(request.method).toBe("PUT");
    expect(new URL(request.url).pathname).toBe("/api/sites/s1/power-evidence");
    expect(await request.json()).toEqual({
      level: "conditions_issued",
      document_date: "2026-10-01",
      conditions_expiry: "2027-10-01",
      attachment_url: "https://drive.example.org/f/1",
    });
  });

  it("refuses a link that is not http(s) before sending anything", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    renderWithProviders(<PowerEvidenceEditor siteId="s1" evidence={none} />);
    await userEvent.type(screen.getByLabelText("Link do dokumentu"), "javascript:alert(1)");
    expect(screen.getByRole("alert")).toHaveTextContent("http://");
    expect(screen.getByRole("button", { name: "Zapisz dowód mocy" })).toBeDisabled();
    expect(puts(fetchMock)).toHaveLength(0);
  });
});
