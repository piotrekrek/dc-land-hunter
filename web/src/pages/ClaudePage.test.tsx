import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/render";

import { EXAMPLE_PROMPTS, claudeCodeCommand, invitation } from "@/lib/claude";

import { ClaudePage } from "./ClaudePage";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const URL_ = "https://dclh.example/mcp";
const ANN = { client_id: "c-ann", client_name: "Claude", connected_at: "2026-11-03T10:00:00Z" };
const BOB = { email: "bob@example.org", role: "member", mcp_access: false, connections: 0 };

interface Setup {
  role?: "admin" | "member";
  mcpAccess?: boolean;
  connectorUrl?: string | null;
  items?: (typeof ANN)[];
  deleteStatus?: number;
  putStatus?: number;
}

/** Answers /api/auth/me, the connections list and /api/team; records the calls. */
function stub({
  role = "member",
  mcpAccess = true,
  connectorUrl = URL_,
  items = [ANN],
  deleteStatus = 204,
  putStatus = 200,
}: Setup = {}) {
  const calls: string[] = [];
  let current = items;
  let bob = BOB;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    const path = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
    calls.push(`${request.method} ${path}`);
    if (path === "/api/auth/me") return json({ email: "ala@example.org", role, mcp_access: mcpAccess });
    if (path === "/api/team") return json([bob]);
    if (path === "/api/team/bob@example.org/mcp-access") {
      if (putStatus !== 200) return json({ detail: "x" }, putStatus);
      const body = (await request.json()) as { on: boolean };
      bob = { ...bob, mcp_access: body.on };
      return json(bob);
    }
    if (request.method === "DELETE") {
      if (deleteStatus === 204) current = [];
      return new Response(null, { status: deleteStatus });
    }
    return json({ connector_url: connectorUrl, items: current });
  });
  return calls;
}

describe("ClaudePage", () => {
  it("shows the address, the claude.ai steps and that the account has access", async () => {
    stub();
    renderWithProviders(<ClaudePage />);
    expect(await screen.findByText(URL_)).toBeInTheDocument();
    expect(await screen.findByText("Twoje konto ma dostęp")).toBeInTheDocument();
    expect(screen.getByText(/Customize → Connectors, kliknij \+ Add, potem Add custom connector/)).toBeInTheDocument();
    expect(screen.getByText(/Nazwa: DC Land Hunter\. Adres: powyższy\./)).toBeInTheDocument();
    expect(screen.getByText(/zaloguj się do DC Land Hunter i wybierz Zezwól/)).toBeInTheDocument();
    expect(screen.getByText(/aplikacja Claude użyje konektora dodanego w przeglądarce/)).toBeInTheDocument();
  });

  it("tells a user without access to ask an administrator", async () => {
    stub({ mcpAccess: false });
    renderWithProviders(<ClaudePage />);
    expect(await screen.findByText("Brak dostępu: poproś administratora o włączenie")).toBeInTheDocument();
    expect(screen.queryByText("Twoje konto ma dostęp")).not.toBeInTheDocument();
  });

  it("says the connector is off, with no steps, when the server has no public address", async () => {
    stub({ connectorUrl: null });
    renderWithProviders(<ClaudePage />);
    expect(await screen.findByText(/Połączenie z Claude jest wyłączone na tym serwerze/)).toBeInTheDocument();
    expect(screen.queryByText(/Add custom connector/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Kopiuj adres" })).not.toBeInTheDocument();
  });

  it("copies the address", async () => {
    stub();
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    renderWithProviders(<ClaudePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Kopiuj adres" }));
    expect(writeText).toHaveBeenCalledWith(URL_);
    expect(await screen.findByRole("button", { name: "Skopiowano" })).toBeInTheDocument();
  });

  it("switches to the Claude Code command and copies it", async () => {
    stub();
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    renderWithProviders(<ClaudePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Claude Code" }));
    expect(screen.getByText("claude mcp add --transport http dclh https://dclh.example/mcp")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Kopiuj polecenie" }));
    expect(writeText).toHaveBeenCalledWith(claudeCodeCommand(URL_));
  });

  it("lists example prompts, each copyable", async () => {
    stub();
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    renderWithProviders(<ClaudePage />);
    expect(EXAMPLE_PROMPTS.length).toBeGreaterThanOrEqual(3);
    for (const prompt of EXAMPLE_PROMPTS) expect(await screen.findByText(prompt)).toBeInTheDocument();
    const first = EXAMPLE_PROMPTS[0] ?? "";
    const item = screen.getByText(first).closest("li");
    await userEvent.click(within(item as HTMLElement).getByRole("button", { name: "Kopiuj" }));
    expect(writeText).toHaveBeenCalledWith(first);
  });

  it("lists the connected apps and disconnects one", async () => {
    const calls = stub();
    renderWithProviders(<ClaudePage />);
    const row = (await screen.findByText("Claude", { selector: "li span" })).closest("li");
    expect(row).toHaveTextContent("od 03.11.2026");
    await userEvent.click(within(row as HTMLElement).getByRole("button", { name: "Odłącz" }));
    expect(await screen.findByText("Nie masz połączonych aplikacji.")).toBeInTheDocument();
    expect(calls).toContain("DELETE /api/oauth/connections/c-ann");
  });

  it("says when disconnecting fails", async () => {
    stub({ deleteStatus: 500 });
    renderWithProviders(<ClaudePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Odłącz" }));
    expect(await screen.findByText("Nie udało się odłączyć. Spróbuj ponownie.")).toBeInTheDocument();
  });

  it("hides the team section from a member and never asks for the team", async () => {
    const calls = stub();
    renderWithProviders(<ClaudePage />);
    await screen.findByText(URL_);
    await screen.findByText("Twoje konto ma dostęp");
    expect(screen.queryByTestId("team-access")).not.toBeInTheDocument();
    expect(calls).not.toContain("GET /api/team");
  });

  it("lets an admin switch a teammate's access on", async () => {
    const calls = stub({ role: "admin" });
    renderWithProviders(<ClaudePage />);
    const team = await screen.findByTestId("team-access");
    expect(await within(team).findByText("bob@example.org")).toBeInTheDocument();
    expect(within(team).getByText("Dostęp wyłączony")).toBeInTheDocument();
    await userEvent.click(within(team).getByRole("button", { name: "Włącz dostęp" }));
    expect(await within(team).findByText("Dostęp włączony")).toBeInTheDocument();
    expect(within(team).getByRole("button", { name: "Wyłącz dostęp" })).toBeInTheDocument();
    expect(calls).toContain("PUT /api/team/bob@example.org/mcp-access");
  });

  it("says when switching access fails", async () => {
    stub({ role: "admin", putStatus: 500 });
    renderWithProviders(<ClaudePage />);
    const team = await screen.findByTestId("team-access");
    await userEvent.click(await within(team).findByRole("button", { name: "Włącz dostęp" }));
    await waitFor(() => expect(screen.getByText("Nie udało się zmienić dostępu. Spróbuj ponownie.")).toBeInTheDocument());
  });

  it("copies an invitation with the app address, the connector address and the steps", async () => {
    stub({ role: "admin" });
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    renderWithProviders(<ClaudePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Kopiuj zaproszenie" }));
    const text = writeText.mock.calls[0]?.[0] as string;
    expect(text).toBe(invitation(URL_));
    expect(text).toContain("https://dclh.example i otwórz zakładkę Claude");
    expect(text).toContain(`adres: ${URL_}`);
    expect(text).toContain("Add custom connector");
    expect(text).toContain(claudeCodeCommand(URL_));
  });
});
