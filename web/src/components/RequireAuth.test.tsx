import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppRoutes } from "@/App";
import { renderWithProviders } from "@/test/render";

// MapLibre needs WebGL, which jsdom lacks.
vi.mock("@/components/map/MapView", () => ({ MapView: () => <div data-testid="map" /> }));

const USER = { email: "ala@example.org", role: "member" };

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function pathOf(input: RequestInfo | URL): string {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  return new URL(url, "http://localhost").pathname;
}

function stubApi(handler: (path: string) => Response | Promise<Response>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => handler(pathOf(input)));
}

afterEach(() => vi.restoreAllMocks());

describe("session expiry", () => {
  it("sends the user to the login page when an API call returns 401, then back to the same page", async () => {
    let serverSession = false; // the server has already ended the session
    const sitePaths: string[] = [];
    stubApi((path) => {
      if (path === "/api/auth/me") return json(USER); // the cookie still looks valid at page load
      if (path === "/api/auth/login") {
        serverSession = true;
        return json(USER);
      }
      if (path === "/api/sites/site-a") {
        sitePaths.push(path);
        return serverSession ? json({ detail: "Nie znaleziono" }, 404) : json({ detail: "Wymagane logowanie" }, 401);
      }
      return json({}, 404);
    });

    renderWithProviders(<AppRoutes />, { route: "/lokalizacje/site-a" });
    expect(await screen.findByRole("heading", { name: "Logowanie" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Główna nawigacja" })).not.toBeInTheDocument();

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("E-mail"), "ala@example.org");
    await user.type(screen.getByLabelText("Hasło"), "dlugie-haslo-123");
    await user.click(screen.getByRole("button", { name: "Zaloguj się" }));

    // Back on the page the user came from: the site request runs again (and now gets its own answer).
    expect(await screen.findByText("Nie ma takiej lokalizacji")).toBeInTheDocument();
    expect(sitePaths).toHaveLength(2);
  });
});

describe("failed session check (GET /api/auth/me)", () => {
  it("shows a retry button on a server error instead of logging the user out", async () => {
    let meCalls = 0;
    stubApi((path) => {
      if (path === "/api/auth/me") {
        meCalls += 1;
        return meCalls === 1 ? json({ detail: "Błąd serwera" }, 500) : json(USER);
      }
      return json({}, 404);
    });

    renderWithProviders(<AppRoutes />, { route: "/projekty" });
    expect(await screen.findByText("Nie udało się sprawdzić logowania")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Logowanie" })).not.toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Spróbuj ponownie" }));
    expect(await screen.findByRole("heading", { name: "Moje projekty" })).toBeInTheDocument();
    expect(meCalls).toBe(2);
  });

  it("treats a network failure the same way", async () => {
    let meCalls = 0;
    stubApi((path) => {
      if (path === "/api/auth/me") {
        meCalls += 1;
        if (meCalls === 1) throw new TypeError("Failed to fetch");
        return json(USER);
      }
      return json({}, 404);
    });

    renderWithProviders(<AppRoutes />, { route: "/projekty" });
    expect(await screen.findByText("Nie udało się sprawdzić logowania")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Logowanie" })).not.toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Spróbuj ponownie" }));
    expect(await screen.findByRole("heading", { name: "Moje projekty" })).toBeInTheDocument();
  });

  it("still sends a 401 from /api/auth/me to the login page", async () => {
    stubApi(() => json({ detail: "Wymagane logowanie" }, 401));
    renderWithProviders(<AppRoutes />, { route: "/ustawienia" });
    expect(await screen.findByRole("heading", { name: "Logowanie" })).toBeInTheDocument();
  });
});

describe("logout", () => {
  it("keeps the user signed in and shows a Polish error when the logout request fails", async () => {
    stubApi((path) => {
      if (path === "/api/auth/me") return json(USER);
      if (path === "/api/auth/logout") return json({ detail: "Błąd serwera" }, 500);
      return json({}, 404);
    });

    renderWithProviders(<AppRoutes />, { route: "/projekty" });
    expect(await screen.findByRole("heading", { name: "Moje projekty" })).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Wyloguj się" }));
    expect(await screen.findByText("Nie udało się wylogować. Pozostajesz zalogowany. Spróbuj ponownie.")).toBeInTheDocument();
    expect(screen.getByTestId("current-user")).toHaveTextContent("ala@example.org");
    expect(screen.getByRole("heading", { name: "Moje projekty" })).toBeInTheDocument();
  });

  it("clears the signed-in state when the logout request succeeds", async () => {
    stubApi((path) => {
      if (path === "/api/auth/me") return json(USER);
      if (path === "/api/auth/logout") return new Response(null, { status: 204 });
      return json({}, 404);
    });

    renderWithProviders(<AppRoutes />, { route: "/projekty" });
    expect(await screen.findByRole("heading", { name: "Moje projekty" })).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Wyloguj się" }));
    expect(await screen.findByRole("heading", { name: "Logowanie" })).toBeInTheDocument();
    expect(screen.queryByText(/Nie udało się wylogować/)).not.toBeInTheDocument();
  });
});
