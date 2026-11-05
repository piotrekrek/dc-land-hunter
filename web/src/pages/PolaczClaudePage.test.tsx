import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/render";

import { PolaczClaudePage } from "./PolaczClaudePage";

afterEach(() => vi.restoreAllMocks());

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const READ = "czytać lokalizacje, oceny, pokrycie powiatów i etapy projektów (bez notatek)";
const WRITE = "obserwować lokalizacje i zlecać sprawdzenie działki";
const INFO = {
  client_name: "Claude",
  user_email: "ala@example.org",
  can_connect: true,
  scopes: [
    { id: "dclh.read", label: READ },
    { id: "dclh.write", label: WRITE },
  ],
};
const GONE = "Prośba o połączenie wygasła lub została już użyta. Zacznij łączenie w Claude od nowa.";
const NO_ACCESS = "Twoje konto nie ma włączonego dostępu dla Claude. Poproś administratora.";

interface Call {
  path: string;
  method: string;
  body: unknown;
}

/** Answers the session check and the two consent endpoints; `calls` records what the page sent. */
function stubApi(consent: Response, decision?: Response) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    const path = new URL(request.url, "http://localhost").pathname;
    const body = request.method === "POST" ? await request.clone().json().catch(() => null) : null;
    calls.push({ path, method: request.method, body });
    if (path === "/api/auth/me") return json({ email: "ala@example.org", role: "member", mcp_access: true });
    if (path.endsWith("/decision")) return decision ?? json({}, 500);
    return consent.clone();
  });
  return calls;
}

function open(onRedirect = vi.fn(), route = "/polacz-claude?req=abc123") {
  renderWithProviders(<PolaczClaudePage onRedirect={onRedirect} />, { route });
  return onRedirect;
}

describe("PolaczClaudePage", () => {
  it("shows the app, the account and what Claude may do", async () => {
    stubApi(json(INFO));
    open();
    expect(await screen.findByRole("heading", { name: "Połączyć Claude z DC Land Hunter?" })).toBeInTheDocument();
    expect(screen.getByText("Claude", { selector: "b" })).toBeInTheDocument();
    expect(screen.getByText(/konto: ala@example.org/)).toBeInTheDocument();
    expect(screen.getByText(READ)).toBeInTheDocument();
    expect(screen.getByText(WRITE)).toBeInTheDocument();
    expect(screen.getByText(/Notatki z projektów nie są udostępniane/)).toBeInTheDocument();
  });

  it("approves and follows the redirect the server gives", async () => {
    const redirect = "https://claude.ai/api/mcp/auth_callback?code=c0de&state=st4te";
    const calls = stubApi(json(INFO), json({ redirect_to: redirect }));
    const onRedirect = open();
    await userEvent.click(await screen.findByRole("button", { name: "Zezwól" }));
    await waitFor(() => expect(onRedirect).toHaveBeenCalledWith(redirect));
    const decision = calls.find((c) => c.path.endsWith("/decision"));
    expect(decision?.path).toBe("/api/oauth/requests/abc123/decision");
    expect(decision?.body).toEqual({ approve: true });
  });

  it("refuses with approve: false and still follows the redirect (access_denied goes back to Claude)", async () => {
    const redirect = "https://claude.ai/api/mcp/auth_callback?error=access_denied&state=st4te";
    const calls = stubApi(json(INFO), json({ redirect_to: redirect }));
    const onRedirect = open();
    await userEvent.click(await screen.findByRole("button", { name: "Odmów" }));
    await waitFor(() => expect(onRedirect).toHaveBeenCalledWith(redirect));
    expect(calls.find((c) => c.path.endsWith("/decision"))?.body).toEqual({ approve: false });
  });

  it("shows the server's text for an expired or used request, with no buttons", async () => {
    stubApi(json({ detail: GONE }, 404));
    open();
    expect(await screen.findByText(GONE)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Zezwól" })).not.toBeInTheDocument();
  });

  it("explains a missing request id without calling the API for it", async () => {
    const calls = stubApi(json(INFO));
    open(vi.fn(), "/polacz-claude");
    expect(await screen.findByText(GONE)).toBeInTheDocument();
    expect(calls.some((c) => c.path.startsWith("/api/oauth"))).toBe(false);
  });

  it("tells a user without access to ask an admin, and offers only to refuse", async () => {
    stubApi(json({ ...INFO, can_connect: false }));
    open();
    expect(await screen.findByText(NO_ACCESS)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Zezwól" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Odmów" })).toBeEnabled();
  });

  it("shows the server's refusal and does not navigate", async () => {
    stubApi(json(INFO), json({ detail: NO_ACCESS }, 403));
    const onRedirect = open();
    await userEvent.click(await screen.findByRole("button", { name: "Zezwól" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(NO_ACCESS);
    expect(onRedirect).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Zezwól" })).toBeEnabled(); // the user can try again
  });

  it.each(["javascript:alert(1)", "http://evil.example/cb"])("does not follow an unsafe redirect (%s)", async (redirect) => {
    stubApi(json(INFO), json({ redirect_to: redirect }));
    const onRedirect = open();
    await userEvent.click(await screen.findByRole("button", { name: "Zezwól" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Nieprawidłowy adres powrotu do Claude");
    expect(onRedirect).not.toHaveBeenCalled();
  });
});
