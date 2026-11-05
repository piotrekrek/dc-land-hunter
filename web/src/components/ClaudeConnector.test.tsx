import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/render";

import { ClaudeConnector } from "./ClaudeConnector";

afterEach(() => vi.restoreAllMocks());

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const URL_ = "https://dclh.example/mcp";
const ANN = { client_id: "c-ann", client_name: "Claude", connected_at: "2026-11-03T10:00:00Z" };

interface Setup {
  mcpAccess?: boolean;
  connectorUrl?: string | null;
  items?: (typeof ANN)[];
  listStatus?: number;
  deleteStatus?: number;
}

/** Answers /api/auth/me and the connections list (which a successful DELETE empties); records the calls. */
function stub({ mcpAccess = true, connectorUrl = URL_, items = [ANN], listStatus = 200, deleteStatus = 204 }: Setup = {}) {
  const calls: string[] = [];
  let current = items;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    const path = new URL(request.url, "http://localhost").pathname;
    calls.push(`${request.method} ${path}`);
    if (path === "/api/auth/me") return json({ email: "ala@example.org", role: "member", mcp_access: mcpAccess });
    if (request.method === "DELETE") {
      if (deleteStatus === 204) current = [];
      return new Response(null, { status: deleteStatus });
    }
    return listStatus === 200 ? json({ connector_url: connectorUrl, items: current }) : json({ detail: "x" }, listStatus);
  });
  return calls;
}

describe("ClaudeConnector", () => {
  it("shows the address, the three steps and that the account has access", async () => {
    stub();
    renderWithProviders(<ClaudeConnector />);
    expect(await screen.findByText(URL_)).toBeInTheDocument();
    expect(await screen.findByText("Twoje konto ma dostęp.")).toBeInTheDocument();
    expect(screen.getByText(/Ustawienia → Konektory → Dodaj własny konektor/)).toBeInTheDocument();
    expect(screen.getByText(/Nazwa: DC Land Hunter\. Adres: powyższy\./)).toBeInTheDocument();
    expect(screen.getByText(/Kliknij Połącz, zaloguj się i wybierz Zezwól\./)).toBeInTheDocument();
    expect(screen.getByText("Claude na telefonie użyje tego samego konektora.")).toBeInTheDocument();
  });

  it("tells a user without access to ask an administrator", async () => {
    stub({ mcpAccess: false });
    renderWithProviders(<ClaudeConnector />);
    expect(await screen.findByText("Poproś administratora o włączenie dostępu dla Claude.")).toBeInTheDocument();
    expect(screen.queryByText("Twoje konto ma dostęp.")).not.toBeInTheDocument();
  });

  it("says the connector is off, with no steps, when the server has no public address", async () => {
    stub({ connectorUrl: null });
    renderWithProviders(<ClaudeConnector />);
    expect(await screen.findByText(/Połączenie z Claude jest wyłączone na tym serwerze/)).toBeInTheDocument();
    expect(screen.queryByText(/Dodaj własny konektor/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Kopiuj adres" })).not.toBeInTheDocument();
  });

  it("copies the address", async () => {
    stub();
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    renderWithProviders(<ClaudeConnector />);
    await userEvent.click(await screen.findByRole("button", { name: "Kopiuj adres" }));
    expect(writeText).toHaveBeenCalledWith(URL_);
    expect(await screen.findByRole("button", { name: "Skopiowano" })).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("lists the connected apps with the date and disconnects one", async () => {
    const calls = stub();
    renderWithProviders(<ClaudeConnector />);
    const row = (await screen.findByText(/od 03\.11\.2026/)).closest("li") as HTMLElement;
    expect(within(row).getByText(/Claude/)).toBeInTheDocument();
    await userEvent.click(within(row).getByRole("button", { name: "Odłącz" }));
    expect(await screen.findByText("Nie masz połączonych aplikacji.")).toBeInTheDocument();
    expect(calls).toContain("DELETE /api/oauth/connections/c-ann");
  });

  it("says so when disconnecting fails, and keeps the app listed", async () => {
    stub({ deleteStatus: 500 });
    renderWithProviders(<ClaudeConnector />);
    await userEvent.click(await screen.findByRole("button", { name: "Odłącz" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Nie udało się odłączyć. Spróbuj ponownie.");
    await waitFor(() => expect(screen.getByRole("button", { name: "Odłącz" })).toBeEnabled());
  });

  it("shows an error when the list cannot be loaded", async () => {
    stub({ listStatus: 500 });
    renderWithProviders(<ClaudeConnector />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Nie udało się pobrać informacji o połączeniu z Claude.");
  });
});
